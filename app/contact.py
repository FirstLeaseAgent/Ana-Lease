"""Persist callback requests independently of application capture and authentication."""
import json
import logging
import os
import re
from urllib.parse import urlsplit
from urllib.request import Request as MailRequest, urlopen
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class ContactInput(BaseModel):
    request_id: UUID
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=10, max_length=35)
    intake_id: UUID | None = None

    @field_validator('name')
    @classmethod
    def name_valid(cls, value):
        value = ' '.join(value.split())
        if len(value) < 2 or not any(c.isalpha() for c in value) or any(ord(c) < 32 for c in value):
            raise ValueError('Escribe el nombre de la persona que debemos contactar')
        return value

    @field_validator('phone')
    @classmethod
    def phone_valid(cls, value):
        value = value.strip()
        if not re.fullmatch(r'\+?[0-9 ()\-]+', value):
            raise ValueError('Escribe un teléfono válido, con lada')
        digits = re.sub(r'[^0-9]', '', value)
        if not 10 <= len(digits) <= 15 or len(set(digits)) < 2:
            raise ValueError('Escribe un teléfono de 10 a 15 dígitos, con lada')
        return ('+' if value.startswith('+') else '') + digits


def send_contact(row):
    url = os.environ['N8N_MAIL_WEBHOOK_URL']
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc != 'flagent.app.n8n.cloud' or parts.username or parts.password or parts.fragment:
        raise RuntimeError('Invalid contact webhook')
    origin = os.environ.get('PUBLIC_ORIGIN') or os.environ['RENDER_EXTERNAL_URL']
    payload = {'event': 'contact_requested', 'email': 'aortega@firstlease.com.mx',
               'contact_id': str(row['id']), 'name': row['name'], 'phone': row['phone'],
               'followup_url': origin + '/seguimiento?contacto=' + str(row['id'])}
    req = MailRequest(url, data=json.dumps(payload).encode(), method='POST',
                      headers={'Content-Type': 'application/json', 'X-AnaLease-Token': os.environ['N8N_MAIL_WEBHOOK_TOKEN']})
    with urlopen(req, timeout=15) as response:
        if response.status != 200 or json.load(response) != {'ok': True}:
            raise RuntimeError('Contact notice unconfirmed')


def deliver(pool, contact_id):
    with pool.connection() as conn:
        row = conn.execute("UPDATE contact_requests SET notification_status='sending',attempts=attempts+1,updated_at=now() WHERE id=%s AND (notification_status IN ('pending','failed') OR (notification_status='sending' AND updated_at<now()-interval '2 minutes')) RETURNING id,name,phone", (contact_id,)).fetchone()
    if not row:
        return
    try:
        send_contact(row)
        status = 'sent'
    except Exception:
        logger.warning('Contact notice unconfirmed; saved for team follow-up')
        status = 'failed'
    with pool.connection() as conn:
        conn.execute('UPDATE contact_requests SET notification_status=%s,updated_at=now() WHERE id=%s', (status, contact_id))


def install(app, pool, owner, digest, client_ip):
    def admin(conn, request):
        uid = owner(request)
        row = conn.execute('SELECT email FROM users WHERE id=%s', (uid,)).fetchone()
        allowed = {s.strip().casefold() for s in os.environ.get('CAPTURE_CONFIG_ADMIN_EMAILS', '').split(',') if s.strip()}
        if not row or row['email'].casefold() not in allowed:
            raise HTTPException(403, 'Este usuario no tiene permiso para consultar contactos')
        return uid

    @app.post('/contact-requests', status_code=202)
    def create(body: ContactInput, request: Request):
        # A callback can be requested before OTP. An intake link is accepted only for its owner.
        uid = None
        if body.intake_id or request.cookies.get('al_session'):
            try:
                uid = owner(request)
            except HTTPException as error:
                if body.intake_id or error.status_code != 401:
                    raise
        ip_hash = digest('contact-ip:' + client_ip(request))
        phone_hash = digest('contact-phone:' + body.phone.lstrip('+'))
        with pool.connection() as conn:
            with conn.transaction():
                conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('contact-id:' + str(body.request_id),))
                previous = conn.execute('SELECT name,phone,intake_id,owner_id FROM contact_requests WHERE id=%s', (body.request_id,)).fetchone()
                if previous:
                    if (previous['name'], previous['phone'], str(previous['intake_id'] or ''), str(previous['owner_id'] or '')) != (body.name, body.phone, str(body.intake_id or ''), str(uid or '')):
                        raise HTTPException(409, 'Esta petición ya se registró con otros datos. Inicia una nueva petición de contacto.')
                    return {'ok': True, 'message': 'Tu solicitud de contacto quedó registrada.'}
                if body.intake_id and not conn.execute('SELECT id FROM intakes WHERE id=%s AND owner_id=%s', (body.intake_id, uid)).fetchone():
                    raise HTTPException(404, 'Solicitud no encontrada')
                conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (phone_hash,))
                conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (ip_hash,))
                phone_count = conn.execute("SELECT count(*) AS n FROM contact_requests WHERE phone_hash=%s AND created_at>now()-interval '1 hour'", (phone_hash,)).fetchone()['n']
                ip_count = conn.execute("SELECT count(*) AS n FROM contact_requests WHERE ip_hash=%s AND created_at>now()-interval '1 hour'", (ip_hash,)).fetchone()['n']
                if phone_count >= 3 or ip_count >= 50:
                    raise HTTPException(429, 'Ya recibimos varias peticiones. Intenta más tarde.')
                conn.execute('INSERT INTO contact_requests(id,name,phone,phone_hash,ip_hash,owner_id,intake_id) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                             (body.request_id, body.name, body.phone, phone_hash, ip_hash, uid, body.intake_id))
        deliver(pool, body.request_id)
        return {'ok': True, 'message': 'Tu solicitud de contacto quedó registrada.'}

    @app.get('/admin/contact-requests')
    def listing(request: Request, offset: int = 0, contact_id: UUID | None = None):
        if offset < 0:
            raise HTTPException(422, 'Página inválida')
        with pool.connection() as conn:
            admin(conn, request)
            rows = conn.execute('SELECT c.id,c.name,c.phone,c.intake_id,c.notification_status,c.created_at,i.rfc FROM contact_requests c LEFT JOIN intakes i ON i.id=c.intake_id WHERE (%s::uuid IS NULL OR c.id=%s) ORDER BY c.created_at DESC,c.id LIMIT 51 OFFSET %s', (contact_id, contact_id, offset)).fetchall()
        return {'contacts': rows[:50], 'has_more': len(rows) > 50}

    @app.post('/admin/contact-requests/{contact_id}/notification/retry')
    def retry(contact_id: UUID, request: Request):
        with pool.connection() as conn:
            admin(conn, request)
            if not conn.execute('SELECT id FROM contact_requests WHERE id=%s', (contact_id,)).fetchone():
                raise HTTPException(404, 'Petición no encontrada')
        deliver(pool, contact_id)
        return {'ok': True}
