"""Anonymous quotes: validate terms, keep secrets server-side and cache safe retries."""
import hmac
import json
import os
import re
from decimal import Decimal, InvalidOperation
from urllib.request import Request as WebRequest, urlopen, HTTPRedirectHandler, build_opener

from fastapi import HTTPException, Request, Response
from pydantic import Field
from .contact import ContactInput


class QuoteInput(ContactInput):
    asset: str = Field(min_length=2, max_length=160)
    value: str = Field(min_length=1, max_length=60)
    down_payment: str = Field(min_length=1, max_length=60)


def number(text):
    text = str(text).strip()
    if re.fullmatch(r'\d{1,3}(,\d{3})+(\.\d+)?', text):
        text = text.replace(',', '')
    elif re.fullmatch(r'\d+([.,]\d+)?', text):
        text = text.replace(',', '.')
    else:
        raise ValueError('Escribe una cantidad válida; ejemplo: 500000 o 500,000.')
    return Decimal(text)


def money(text):
    text = str(text).lower().strip()
    text = re.sub(r'^\$\s*', '', text)
    text = re.sub(r'\s*(pesos|mxn)$', '', text).strip()
    thousands = bool(re.search(r'(mil|k)$', text))
    text = re.sub(r'\s*(mil|k)$', '', text).strip()
    return number(text) * (1000 if thousands else 1)


def normalize(body):
    if len(body.asset.strip()) < 2 or any(ord(c) < 32 for c in body.asset):
        raise HTTPException(422, 'Indica el activo que deseas cotizar.')
    try:
        value = money(body.value)
        if not 0 < value <= 100000000 or value != value.quantize(Decimal('.01')):
            raise ValueError('Indica el valor del activo en pesos, mayor que cero y con máximo dos decimales.')
        text = body.down_payment.lower().strip()
        percent = bool(re.search(r'(%|por ciento)$', text))
        amount = bool(re.search(r'\$|\b(pesos|mxn|mil)\b|\d\s*k$', text))
        if percent and amount:
            raise ValueError('Indica el enganche en porcentaje o en pesos, sin mezclar unidades.')
        if amount:
            down = money(text) / value * 100
        else:
            down = number(re.sub(r'\s*(%|por ciento)$', '', text).strip())
            if not percent and down == 1:
                raise ValueError('Aclara el enganche usando % o pesos. Debe ser entre 10% y 40%.')
            if not percent:
                if 0 < down < 1: down *= 100
                elif down > 100: down = down / value * 100
        if not Decimal(10) <= down <= Decimal(40):
            raise ValueError('El enganche debe estar entre el 10% y el 40% del valor del activo.')
    except (ValueError, InvalidOperation) as exc:
        raise HTTPException(422, str(exc))
    return {'nombre':body.name, 'nombre_activo':body.asset.strip(), 'phone_number':body.phone,
            'valor':float(value), 'enganche':float(down)}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def generate(payload):
    # Only this configured endpoint receives the server credential. No redirects.
    url = os.environ.get('N8N_QUOTE_WEBHOOK_URL', '')
    token = os.environ.get('N8N_QUOTE_WEBHOOK_TOKEN', '')
    if url != 'https://flagent.app.n8n.cloud/webhook/Analease-Cotizacion' or not token:
        raise HTTPException(503, 'La cotización no está disponible por el momento. Solicita que te contacten.')
    request = WebRequest(url, data=json.dumps(payload).encode(), method='POST',
                         headers={'Content-Type':'application/json','X-AnaLease-Token':token})
    try:
        with build_opener(NoRedirect()).open(request, timeout=90) as response:
            pdf = response.read(5 * 1024 * 1024 + 1)
            if response.status != 200 or len(pdf) > 5 * 1024 * 1024 or not pdf.startswith(b'%PDF-'):
                raise ValueError('Invalid PDF response')
    except Exception:
        # A timeout may follow a successful SharePoint write: never retry automatically.
        raise HTTPException(503, 'No pudimos confirmar la entrega del PDF. Pide que te contacten antes de generar otra cotización.')
    return pdf


def install(app, pool, digest, client_ip):
    @app.post('/quotes/preview')
    def preview(body: QuoteInput):
        payload = normalize(body)
        return {'percentage':payload['enganche'], 'amount':round(payload['valor']*payload['enganche']/100,2), 'value':payload['valor']}

    @app.post('/quotes')
    def quote(body: QuoteInput, request: Request):
        payload = normalize(body)
        if not os.environ.get('N8N_QUOTE_WEBHOOK_TOKEN'):
            raise HTTPException(503, 'La cotización no está disponible por el momento. Solicita que te contacten.')
        fingerprint = digest('quote:' + json.dumps(payload,sort_keys=True))
        ip_hash = digest('quote-ip:' + client_ip(request))
        with pool.connection() as conn:
            with conn.transaction():
                conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('quote:'+str(body.request_id),))
                previous = conn.execute('SELECT payload_hash,status,pdf,created_at>now()-interval \'1 day\' AS fresh FROM quote_dispatches WHERE id=%s', (body.request_id,)).fetchone()
                if previous:
                    if not hmac.compare_digest(previous['payload_hash'], fingerprint):
                        raise HTTPException(409,'Esta petición ya se utilizó con otros datos.')
                    if previous['status']=='ready' and previous['fresh'] and previous['pdf']:
                        return pdf_response(bytes(previous['pdf']))
                    raise HTTPException(409,'La cotización ya fue solicitada. Si no recibiste el PDF, pide que te contacten; no generaremos otra automáticamente.')
                conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('quote-limits',))
                counts = conn.execute("SELECT count(*) AS total,count(*) FILTER(WHERE ip_hash=%s) AS ip FROM quote_dispatches WHERE created_at>now()-interval '1 hour'", (ip_hash,)).fetchone()
                if counts['total'] >= 100 or counts['ip'] >= 20:
                    raise HTTPException(429,'Ya recibimos varias cotizaciones. Intenta más tarde o pide que te contacten.')
                conn.execute("UPDATE quote_dispatches SET pdf=NULL WHERE pdf IS NOT NULL AND created_at<now()-interval '1 day'")
                conn.execute("INSERT INTO quote_dispatches(id,payload_hash,ip_hash,status) VALUES(%s,%s,%s,'processing')", (body.request_id,fingerprint,ip_hash))
        try:
            pdf = generate(payload)
        except HTTPException:
            with pool.connection() as conn:
                conn.execute("UPDATE quote_dispatches SET status='unconfirmed' WHERE id=%s", (body.request_id,))
            raise
        with pool.connection() as conn:
            conn.execute("UPDATE quote_dispatches SET status='ready',pdf=%s WHERE id=%s", (pdf,body.request_id))
        return pdf_response(pdf)


def pdf_response(pdf):
    return Response(pdf,media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="FirstLease-Cotizacion.pdf"'})
