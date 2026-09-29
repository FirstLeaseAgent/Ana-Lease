import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .rules import FIELDS, allowed_field, masked_name, normalized_rfc, participant_from_rfc
from .mail import send_code
from .syntage import SyntageUnavailable, check_status

pool = ConnectionPool(conninfo=os.environ.get('DATABASE_URL', ''), min_size=0, max_size=5, open=False, kwargs={'row_factory': dict_row})
COOKIE = 'al_session'

@asynccontextmanager
async def lifespan(app):
    for key in ('DATABASE_URL', 'OTP_PEPPER', 'N8N_MAIL_WEBHOOK_URL', 'N8N_MAIL_WEBHOOK_TOKEN'):
        if not os.environ.get(key):
            raise RuntimeError(f'Falta configuración: {key}')
    if not (os.environ.get('PUBLIC_ORIGIN') or os.environ.get('RENDER_EXTERNAL_URL')):
        raise RuntimeError('Falta configuración: PUBLIC_ORIGIN o RENDER_EXTERNAL_URL')
    pool.open()
    yield
    pool.close()

app = FastAPI(title='AnaLease captura', docs_url=None, redoc_url=None, lifespan=lifespan)

@app.middleware('http')
async def security(request: Request, call_next):
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        origin = request.headers.get('origin')
        if origin != (os.environ.get('PUBLIC_ORIGIN') or os.environ.get('RENDER_EXTERNAL_URL')):
            return Response(status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    return response

def digest(value: str) -> str:
    return hmac.new(os.environ['OTP_PEPPER'].encode(), value.encode(), hashlib.sha256).hexdigest()

def client_ip(request: Request) -> str:
    # Starlette uses the direct ASGI peer. Do not trust client-supplied forwarded headers.
    return request.client.host if request.client else 'unknown'

class EmailInput(BaseModel):
    email: EmailStr

class VerifyInput(EmailInput):
    code: str = Field(pattern=r'^\d{6}$')

class RfcInput(BaseModel):
    rfc: str = Field(max_length=20)

class ParticipantInput(BaseModel):
    role: str
    rfc: str = Field(min_length=1, max_length=20)

class AnswerInput(BaseModel):
    field_code: str = Field(max_length=80)
    value: str = Field(min_length=1, max_length=500)

def authorization_link(rfc: str):
    try:
        status = check_status(rfc)
    except (SyntageUnavailable, KeyError):
        raise HTTPException(503, 'No pudimos verificar la autorización; intenta más tarde')
    if status['next_action'] != 'onboarding':
        return None
    kind = 'legal' if len(rfc) == 12 else 'physical'
    return f'https://registro.syntage.com/8de4ef?reporteDeCredito=true&personType={kind}'

def owner(request: Request) -> str:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, 'Verifica tu correo')
    with pool.connection() as conn:
        row = conn.execute('SELECT user_id FROM sessions WHERE token_hash=%s AND expires_at>now()', (digest('session:'+token),)).fetchone()
    if not row:
        raise HTTPException(401, 'Sesión vencida')
    return str(row['user_id'])

def intake_for_owner(conn, intake_id: UUID, user_id: str, editable=False):
    row = conn.execute('SELECT id, status FROM intakes WHERE id=%s AND owner_id=%s FOR UPDATE', (intake_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, 'Captura no encontrada')
    if editable and row['status'] != 'open':
        raise HTTPException(409, 'Captura enviada')
    return row

@app.get('/health')
def health():
    try:
        with pool.connection(timeout=2) as conn:
            conn.execute('SELECT 1')
    except Exception:
        raise HTTPException(503, 'Base de datos no disponible')
    return {'status': 'ok'}

@app.get('/', response_class=HTMLResponse)
def home():
    return Path(__file__).resolve().parent.parent.joinpath('static/index.html').read_text()

@app.get('/app.js')
def javascript():
    return Response(Path(__file__).resolve().parent.parent.joinpath('static/app.js').read_text(), media_type='application/javascript')

@app.get('/app.css')
def stylesheet():
    return Response(Path(__file__).resolve().parent.parent.joinpath('static/app.css').read_text(), media_type='text/css')

@app.post('/auth/start', status_code=202)
def auth_start(body: EmailInput, request: Request):
    email = str(body.email).strip().lower()
    ip_hash = digest('ip:'+client_ip(request))
    code = f'{secrets.randbelow(1000000):06d}'
    with pool.connection() as conn:
        with conn.transaction():
            # Serialized across simultaneous requests from an email or IP; generic response avoids enumeration.
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (email,))
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (ip_hash,))
            email_count = conn.execute("SELECT count(*) AS n FROM otp_challenges WHERE email=%s AND created_at>now()-interval '1 hour'", (email,)).fetchone()['n']
            ip_count = conn.execute("SELECT count(*) AS n FROM otp_challenges WHERE ip_hash=%s AND created_at>now()-interval '1 hour'", (ip_hash,)).fetchone()['n']
            recent = conn.execute("SELECT 1 FROM otp_challenges WHERE email=%s AND created_at>now()-interval '60 seconds' LIMIT 1", (email,)).fetchone()
            if email_count >= 10 or ip_count >= 100 or recent:
                return {'message': 'Si es posible, recibirás un código en tu correo.'}
            row = conn.execute("INSERT INTO otp_challenges(email,ip_hash,code_hash,expires_at) VALUES(%s,%s,%s,now()+interval '10 minutes') RETURNING id", (email, ip_hash, digest(email+':'+code))).fetchone()
    try:
        send_code(email, code)
    except Exception:
        with pool.connection() as conn:
            conn.execute('UPDATE otp_challenges SET used_at=now() WHERE id=%s', (row['id'],))
        raise HTTPException(503, 'No pudimos enviar el código; intenta más tarde')
    return {'message': 'Si es posible, recibirás un código en tu correo.'}

@app.post('/auth/verify')
def auth_verify(body: VerifyInput, response: Response):
    email = str(body.email).strip().lower()
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (email,))
            challenge = conn.execute('SELECT * FROM otp_challenges WHERE email=%s ORDER BY created_at DESC LIMIT 1 FOR UPDATE', (email,)).fetchone()
            if not challenge or challenge['used_at'] or challenge['attempts'] >= 5 or challenge['expires_at'].timestamp() <= __import__('time').time():
                raise HTTPException(400, 'Código inválido o vencido')
            conn.execute('UPDATE otp_challenges SET attempts=attempts+1 WHERE id=%s', (challenge['id'],))
            if not hmac.compare_digest(challenge['code_hash'], digest(email+':'+body.code)):
                # Save failed attempt even though we return an error.
                invalid = True
            else:
                invalid = False
                conn.execute('UPDATE otp_challenges SET used_at=now() WHERE id=%s', (challenge['id'],))
                user = conn.execute('INSERT INTO users(email) VALUES(%s) ON CONFLICT(email) DO UPDATE SET email=EXCLUDED.email RETURNING id', (email,)).fetchone()
                token = secrets.token_urlsafe(32)
                conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(%s,%s,now()+interval '14 days')", (digest('session:'+token), user['id']))
    if invalid:
        raise HTTPException(400, 'Código inválido o vencido')
    response.set_cookie(COOKIE, token, max_age=14*86400, httponly=True, secure=True, samesite='lax', path='/')
    return {'ok': True}

@app.post('/auth/logout')
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with pool.connection() as conn:
            conn.execute('DELETE FROM sessions WHERE token_hash=%s', (digest('session:'+token),))
    response.delete_cookie(COOKIE, path='/')
    return {'ok': True}

@app.get('/intakes')
def list_intakes(request: Request):
    user_id = owner(request)
    with pool.connection() as conn:
        has_intakes = conn.execute('SELECT 1 FROM intakes WHERE owner_id=%s LIMIT 1', (user_id,)).fetchone() is not None
    return {'has_intakes': has_intakes}

@app.post('/intakes')
def create_intake(body: RfcInput, request: Request):
    user_id = owner(request)
    try:
        rfc, subject = normalized_rfc(body.rfc)
    except ValueError:
        raise HTTPException(422, 'Revisa el formato del RFC')
    link = authorization_link(rfc)
    with pool.connection() as conn:
        with conn.transaction():
            row = conn.execute('INSERT INTO intakes(owner_id,rfc) VALUES(%s,%s) ON CONFLICT(owner_id,rfc) DO UPDATE SET updated_at=now() RETURNING id,status', (user_id,rfc)).fetchone()
            conn.execute("INSERT INTO participants(intake_id,role,subject_type,rfc) VALUES(%s,'solicitante',%s,%s) ON CONFLICT DO NOTHING", (row['id'],subject,rfc))
    return {'id': row['id'], 'status': row['status'], 'authorization_url': link}

@app.get('/intakes/{intake_id}')
def read_intake(intake_id: UUID, request: Request):
    user_id = owner(request)
    with pool.connection() as conn:
        intake = intake_for_owner(conn, intake_id, user_id)
        people = conn.execute('SELECT id,role,subject_type FROM participants WHERE intake_id=%s ORDER BY created_at,id', (intake_id,)).fetchall()
        answers = conn.execute('SELECT participant_id,field_code FROM answers WHERE intake_id=%s', (intake_id,)).fetchall()
        names = conn.execute("SELECT participant_id,value_json FROM answers WHERE intake_id=%s AND field_code IN ('nombre','razon_social')", (intake_id,)).fetchall()
    hints = {row['participant_id']: masked_name(row['value_json']) for row in names if isinstance(row['value_json'], str)}
    counts = {'aval': 0, 'representante': 0}
    for person in people:
        role = person['role']
        if role in counts:
            counts[role] += 1
        label = role.capitalize() + (f' {counts[role]}' if role in counts else '')
        person['context'] = f'{label} ({person["subject_type"]})'
        if person['id'] in hints:
            person['context'] += f' · {hints[person["id"]]}'
    return {'intake': intake, 'participants': people, 'answers': answers,
            'fields': {str(p['id']): sorted(FIELDS[p['role']][p['subject_type']]) for p in people}}

@app.post('/intakes/{intake_id}/participants')
def add_participant(intake_id: UUID, body: ParticipantInput, request: Request):
    user_id = owner(request)
    if body.role not in ('aval','representante'):
        raise HTTPException(422, 'Tipo de participante inválido')
    try:
        rfc, subject = participant_from_rfc(body.role, body.rfc)
    except ValueError as exc:
        raise HTTPException(422, 'Revisa el formato del RFC' if str(exc) == 'RFC inválido' else str(exc))
    link = authorization_link(rfc) if body.role == 'aval' else None
    with pool.connection() as conn:
        with conn.transaction():
            intake_for_owner(conn, intake_id, user_id, editable=True)
            if body.role == 'aval':
                n = conn.execute("SELECT count(*) AS n FROM participants WHERE intake_id=%s AND role='aval'", (intake_id,)).fetchone()['n']
                if n >= 3:
                    raise HTTPException(409, 'Máximo tres avales')
            row = conn.execute('INSERT INTO participants(intake_id,role,subject_type,rfc) VALUES(%s,%s,%s,%s) RETURNING id', (intake_id,body.role,subject,rfc)).fetchone()
    return {'id': row['id'], 'authorization_url': link}

@app.put('/intakes/{intake_id}/participants/{participant_id}/answers')
def save_answer(intake_id: UUID, participant_id: UUID, body: AnswerInput, request: Request):
    user_id = owner(request)
    with pool.connection() as conn:
        with conn.transaction():
            intake_for_owner(conn, intake_id, user_id, editable=True)
            person = conn.execute('SELECT role,subject_type FROM participants WHERE id=%s AND intake_id=%s', (participant_id,intake_id)).fetchone()
            if not person or not allowed_field(person['role'], person['subject_type'], body.field_code):
                raise HTTPException(422, 'Campo no admitido')
            conn.execute('INSERT INTO answers(intake_id,participant_id,field_code,value_json) VALUES(%s,%s,%s,%s) ON CONFLICT(participant_id,field_code) DO UPDATE SET value_json=EXCLUDED.value_json,updated_at=now()', (intake_id,participant_id,body.field_code,psycopg.types.json.Jsonb(body.value.strip())))
            conn.execute('UPDATE intakes SET updated_at=now() WHERE id=%s', (intake_id,))
    return {'ok': True}
