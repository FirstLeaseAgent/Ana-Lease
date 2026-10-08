import hashlib
import hmac
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from psycopg.types.json import Jsonb

from .rules import FIELDS, allowed_field, masked_name, normalized_rfc, participant_from_rfc
from .mail import send_code
from .syntage import SyntageUnavailable, check_status
from .conversation import (ConversationUnavailable, InvalidProposal, active_person,
                           apply_proposal, call_agent, context_for_turn, align_direct_answer,
                           reply_after_proposal, resume_reply, history_for_turns, capture_stage, local_reference, capture_progress, identity_audit)
from .memory import load_relations
from .document_routes import install as install_document_routes
from . import catalog
from .catalog_routes import install as install_catalog_routes
from . import shareholders
from .pending import unavailable
from .notifications import deliver as deliver_notice

logger = logging.getLogger(__name__)

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

class ConversationInput(BaseModel):
    request_id: UUID
    message: str = Field(min_length=1, max_length=2000)
    question: str | None = Field(default=None, max_length=2000)

def conversation_enabled_for(conn, user_id):
    enabled = os.environ.get('CAPTURE_AI_ENABLED', '').lower() == 'true'
    allowed = {s.strip().lower() for s in os.environ.get('CAPTURE_AI_ALLOWED_EMAILS', '').split(',') if s.strip()}
    if not enabled or not allowed:
        return False
    user = conn.execute('SELECT email FROM users WHERE id=%s', (user_id,)).fetchone()
    return bool(user and user['email'].lower() in allowed)

def conversation_people(conn, intake_id):
    people = conn.execute("SELECT id,role,subject_type,rfc,company_id,subject_type_confirmed FROM participants WHERE intake_id=%s ORDER BY CASE role WHEN 'solicitante' THEN 0 WHEN 'contacto' THEN 1 WHEN 'representante' THEN 2 WHEN 'accionista' THEN 3 ELSE 4 END,created_at,id", (intake_id,)).fetchall()
    result = {str(p['id']): dict(p, id=str(p['id']), answers={}) for p in people}
    for a in conn.execute('SELECT participant_id,field_code,value_json FROM answers WHERE intake_id=%s', (intake_id,)).fetchall():
        if isinstance(a['value_json'], str):
            result[str(a['participant_id'])]['answers'][a['field_code']] = a['value_json']
    for row in conn.execute('SELECT participant_id,field_code FROM capture_pending_fields WHERE intake_id=%s',(intake_id,)).fetchall():
        person=result.get(str(row['participant_id']))
        if person and row['field_code'] not in person['answers'] and not (row['field_code']=='rfc' and person.get('rfc')):
            person.setdefault('pending_fields',[]).append(row['field_code'])
    config=catalog.snapshot(conn,intake_id)
    controls=conn.execute('SELECT shareholders_enabled,shareholders_complete,guarantors_complete FROM intakes WHERE id=%s',(intake_id,)).fetchone() or {}
    for person in result.values():
        person['catalog']=config
        if person.get('company_id'):person['company_id']=str(person['company_id'])
        if person['role']=='solicitante':person.update({key:bool(controls.get(key)) for key in ('shareholders_enabled','shareholders_complete','guarantors_complete')})
    return result

def authorization_link(rfc: str):
    try:
        status = check_status(rfc)
    except SyntageUnavailable as exc:
        # Log only the failure class, never the RFC, webhook response or token.
        logger.warning('Syntage status check failed: %s', exc)
        raise HTTPException(503, 'No pudimos verificar la autorización; intenta más tarde')
    except KeyError:
        logger.warning('Syntage status check failed: missing server configuration')
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
    row = conn.execute('SELECT id,status,capture_version,capture_active_id FROM intakes WHERE id=%s AND owner_id=%s FOR UPDATE', (intake_id, user_id)).fetchone()
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

@app.get('/firstlease-logo.png')
def brand_logo():
    return Response(Path(__file__).resolve().parent.parent.joinpath('static/firstlease-logo.png').read_bytes(),media_type='image/png')

@app.get('/documents.js')
def documents_javascript():
    return Response(Path(__file__).resolve().parent.parent.joinpath('static/documents.js').read_text(),media_type='application/javascript')

@app.get('/welcome-cars.webp')
def welcome_cars():
    return Response(Path(__file__).resolve().parent.parent.joinpath('static/welcome-cars.webp').read_bytes(), media_type='image/webp')

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
    with pool.connection() as conn:
        existing=conn.execute('SELECT id,status FROM intakes WHERE owner_id=%s AND rfc=%s',(user_id,rfc)).fetchone()
        if existing and existing['status']=='submitted':
            return {'id':existing['id'],'status':'submitted','authorization_url':None,'conversation_enabled':conversation_enabled_for(conn,user_id)}
    link = authorization_link(rfc)
    with pool.connection() as conn:
        with conn.transaction():
            _,config=catalog.active(conn)
            row = conn.execute('INSERT INTO intakes(owner_id,rfc,catalog_snapshot,shareholders_enabled) VALUES(%s,%s,%s,%s) ON CONFLICT(owner_id,rfc) DO UPDATE SET updated_at=now() RETURNING id,status', (user_id,rfc,psycopg.types.json.Jsonb(config),subject=='PM')).fetchone()
            conn.execute("INSERT INTO participants(intake_id,role,subject_type,rfc) VALUES(%s,'solicitante',%s,%s) ON CONFLICT DO NOTHING", (row['id'],subject,rfc))
            enabled = conversation_enabled_for(conn, user_id)
            if enabled:
                contact = conn.execute("INSERT INTO participants(intake_id,role,subject_type) VALUES(%s,'contacto','PF') ON CONFLICT DO NOTHING RETURNING id", (row['id'],)).fetchone()
                if contact:
                    conn.execute('UPDATE intakes SET capture_version=capture_version+1 WHERE id=%s', (row['id'],))
    return {'id': row['id'], 'status': row['status'], 'authorization_url': link, 'conversation_enabled': enabled}

@app.get('/intakes/{intake_id}')
def read_intake(intake_id: UUID, request: Request):
    user_id = owner(request)
    with pool.connection() as conn:
        intake = intake_for_owner(conn, intake_id, user_id)
        people = conn.execute("SELECT id,role,subject_type FROM participants WHERE intake_id=%s ORDER BY CASE role WHEN 'solicitante' THEN 0 WHEN 'contacto' THEN 1 WHEN 'representante' THEN 2 WHEN 'accionista' THEN 3 ELSE 4 END,created_at,id", (intake_id,)).fetchall()
        answers = conn.execute('SELECT participant_id,field_code FROM answers WHERE intake_id=%s', (intake_id,)).fetchall()
        deferred=conn.execute('SELECT participant_id,field_code FROM capture_pending_fields WHERE intake_id=%s',(intake_id,)).fetchall()
        answers += [dict(row,status='pending') for row in deferred]
        names = conn.execute("SELECT participant_id,value_json FROM answers WHERE intake_id=%s AND field_code IN ('nombre','razon_social')", (intake_id,)).fetchall()
        enabled = conversation_enabled_for(conn, user_id)
        configured_people=conversation_people(conn,intake_id)
    hints = {row['participant_id']: masked_name(row['value_json']) for row in names if isinstance(row['value_json'], str)}
    counts = {'aval': 0, 'representante': 0,'accionista':0}
    for person in people:
        role = person['role']
        if role in counts:
            counts[role] += 1
        label = role.capitalize() + (f' {counts[role]}' if role in counts else '')
        person['context'] = f'{label} ({person["subject_type"]})'
        if person['id'] in hints:
            person['context'] += f' · {hints[person["id"]]}'
    return {'intake': intake, 'participants': people, 'answers': answers,
            'fields': {pid: [r['code'] for r in catalog.field_rows(p) or []] for pid,p in configured_people.items()},
            'question_labels':{pid:{f:catalog.question_for(p,f) for f in catalog.fields_for(p)} for pid,p in configured_people.items()},
            'conversation_enabled': enabled,'stage':capture_stage(configured_people),
            'next_reply':resume_reply(configured_people),'next_active_id':active_person(configured_people)}

@app.post('/intakes/{intake_id}/participants')
def add_participant(intake_id: UUID, body: ParticipantInput, request: Request):
    user_id = owner(request)
    if body.role not in ('aval','representante','accionista'):
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
                conn.execute('UPDATE intakes SET guarantors_complete=false WHERE id=%s',(intake_id,))
            company_id=None
            if body.role=='accionista':
                people=conversation_people(conn,intake_id);company=shareholders.applicant(people)
                if not company or company['subject_type']!='PM':raise HTTPException(422,'Solo se agregan accionistas a una empresa solicitante')
                if sum(p['role']=='accionista' for p in people.values())>=shareholders.MAX_SHAREHOLDERS:raise HTTPException(409,'Máximo tres principales accionistas')
                if any(p['role']=='accionista' and p.get('rfc')==rfc for p in people.values()):raise HTTPException(409,'Ese accionista ya está registrado en esta empresa')
                company_id=company['id']
                conn.execute('UPDATE intakes SET shareholders_enabled=true,shareholders_complete=false WHERE id=%s',(intake_id,))
            row = conn.execute('INSERT INTO participants(intake_id,role,subject_type,rfc,company_id) VALUES(%s,%s,%s,%s,%s) RETURNING id', (intake_id,body.role,subject,rfc,company_id)).fetchone()
            conn.execute('UPDATE intakes SET capture_version=capture_version+1 WHERE id=%s', (intake_id,))
    return {'id': row['id'], 'authorization_url': link}

@app.put('/intakes/{intake_id}/participants/{participant_id}/answers')
def save_answer(intake_id: UUID, participant_id: UUID, body: AnswerInput, request: Request):
    user_id = owner(request)
    with pool.connection() as conn:
        with conn.transaction():
            intake_for_owner(conn, intake_id, user_id, editable=True)
            person=conversation_people(conn,intake_id).get(str(participant_id))
            if not person or body.field_code not in catalog.fields_for(person):
                raise HTTPException(422, 'Campo no admitido')
            if unavailable(body.value,body.field_code):
                if person['answers'].get(body.field_code):raise HTTPException(409,'Ese dato ya está registrado')
                expected=active_person({str(participant_id):person},str(participant_id))
                from .conversation import missing
                if not expected or missing(person)[0]!=body.field_code:raise HTTPException(409,'Solo puedes dejar pendiente la pregunta actual')
                conn.execute('INSERT INTO capture_pending_fields(intake_id,participant_id,field_code) VALUES(%s,%s,%s) ON CONFLICT(participant_id,field_code) DO NOTHING',(intake_id,participant_id,body.field_code))
                conn.execute('UPDATE intakes SET updated_at=now(),capture_version=capture_version+1 WHERE id=%s',(intake_id,))
                return {'ok':True,'pending':True}
            try:catalog.validate_value(person,body.field_code,body.value.strip())
            except ValueError as exc:raise HTTPException(422,str(exc))
            if person['role']=='accionista':
                updated=conversation_people(conn,intake_id)
                existing=person['answers'].get(body.field_code)
                if existing and existing!=body.value.strip():raise HTTPException(409,'Ese dato ya está registrado; confirma la corrección con nuestro equipo')
                updated[str(participant_id)]['answers'][body.field_code]=body.value.strip()
                try:shareholders.validate_people(updated)
                except ValueError as exc:raise HTTPException(422,str(exc))
            conn.execute('INSERT INTO answers(intake_id,participant_id,field_code,value_json) VALUES(%s,%s,%s,%s) ON CONFLICT(participant_id,field_code) DO UPDATE SET value_json=EXCLUDED.value_json,updated_at=now()', (intake_id,participant_id,body.field_code,psycopg.types.json.Jsonb(body.value.strip())))
            conn.execute('DELETE FROM capture_pending_fields WHERE intake_id=%s AND participant_id=%s AND field_code=%s',(intake_id,participant_id,body.field_code))
            conn.execute('UPDATE intakes SET updated_at=now(),capture_version=capture_version+1 WHERE id=%s', (intake_id,))
    return {'ok': True}

@app.get('/intakes/{intake_id}/conversation')
def resume_conversation(intake_id: UUID, request: Request):
    user_id = owner(request)
    with pool.connection() as conn:
        intake = intake_for_owner(conn, intake_id, user_id, editable=True)
        if not conversation_enabled_for(conn, user_id):
            raise HTTPException(404, 'Conversación no disponible')
        people = conversation_people(conn, intake_id)
        preferred = str(intake['capture_active_id']) if intake['capture_active_id'] else None
    return {'reply': resume_reply(people, preferred), 'active_id': active_person(people, preferred), 'authorization_links': [], 'stage':capture_stage(people,preferred),'progress':capture_progress(people)}

@app.post('/intakes/{intake_id}/conversation')
def converse(intake_id: UUID, body: ConversationInput, request: Request):
    user_id = owner(request)
    message = body.message.strip()
    if not message:
        raise HTTPException(422, 'Escribe un mensaje')
    with pool.connection() as conn:
        with conn.transaction():
            intake = intake_for_owner(conn, intake_id, user_id, editable=True)
            if not conversation_enabled_for(conn, user_id):
                raise HTTPException(404, 'Conversación no disponible')
            previous = conn.execute('SELECT * FROM capture_turns WHERE intake_id=%s AND request_id=%s', (intake_id, body.request_id)).fetchone()
            if previous and previous['user_message'] != message:
                raise HTTPException(409, 'La petición ya corresponde a otro mensaje')
            if previous and previous['status'] == 'complete':
                return previous['response_json']
            if previous and previous['status'] == 'processing':
                age = __import__('time').time() - previous['updated_at'].timestamp()
                if age < 120:
                    raise HTTPException(409, 'Ese mensaje se está procesando; espera un momento')
            usage = conn.execute("SELECT COALESCE(sum(t.attempts),0) AS n FROM capture_turns t JOIN intakes i ON i.id=t.intake_id WHERE i.owner_id=%s AND t.created_at>now()-interval '1 hour'", (user_id,)).fetchone()['n']
            if usage >= 100:
                raise HTTPException(429, 'Has enviado muchos mensajes; intenta más tarde')
            conn.execute("INSERT INTO capture_turns(intake_id,request_id,user_message,status) VALUES(%s,%s,%s,'processing') ON CONFLICT(intake_id,request_id) DO UPDATE SET status='processing',attempts=capture_turns.attempts+1,updated_at=now()", (intake_id,body.request_id,message))
            people = conversation_people(conn, intake_id)
            preferred = str(intake['capture_active_id']) if intake['capture_active_id'] else None
            version = intake['capture_version']
            session_email = conn.execute('SELECT email FROM users WHERE id=%s', (user_id,)).fetchone()['email']
            history_rows = conn.execute("SELECT user_message,response_json,audit_json FROM capture_turns WHERE intake_id=%s AND status='complete' ORDER BY updated_at DESC,request_id DESC LIMIT 12", (intake_id,)).fetchall()
            history = history_for_turns(history_rows, people)
            relations = load_relations(conn,intake_id,people,session_email)
    try:
        context, history = context_for_turn(people, preferred, history, body.question, session_email, relations)
        proposal = local_reference(people,message,preferred,history,relations) or call_agent(message, context, history)
        proposal, alignment = align_direct_answer(proposal, people, message, preferred, context['current_question']['text'])
        updated, audit, active = apply_proposal(people, proposal, message, preferred, session_email)
        audit.extend(identity_audit(updated,audit,message))
        if alignment:
            audit.append(alignment)
        links = []
        for pid, person in updated.items():
            if person['role'] == 'aval' and person.get('rfc') and person.get('rfc') != people.get(pid, {}).get('rfc'):
                url = authorization_link(person['rfc'])
                if url:
                    links.append({'url': url, 'context': person['role'].capitalize()})
        reply = reply_after_proposal(proposal, updated, audit, active)
        response = {'reply': reply, 'active_id': active, 'authorization_links': links,'stage':capture_stage(updated,active),'progress':capture_progress(updated)}
        with pool.connection() as conn:
            with conn.transaction():
                current = intake_for_owner(conn, intake_id, user_id, editable=True)
                if current['capture_version'] != version:
                    raise HTTPException(409, 'La captura cambió en otra pantalla; recarga para continuar')
                if not conversation_enabled_for(conn, user_id):
                    raise HTTPException(404, 'Conversación no disponible')
                for pid, person in updated.items():
                    if pid not in people:
                        conn.execute('INSERT INTO participants(id,intake_id,role,subject_type,rfc,subject_type_confirmed,company_id) VALUES(%s,%s,%s,%s,%s,%s,%s)', (pid,intake_id,person['role'],person['subject_type'],person['rfc'],person.get('subject_type_confirmed',True),person.get('company_id')))
                    elif (person['rfc'],person['subject_type'],person.get('subject_type_confirmed',True)) != (people[pid]['rfc'],people[pid]['subject_type'],people[pid].get('subject_type_confirmed',True)):
                        conn.execute('UPDATE participants SET rfc=%s,subject_type=%s,subject_type_confirmed=%s WHERE id=%s AND intake_id=%s', (person['rfc'],person['subject_type'],person.get('subject_type_confirmed',True),pid,intake_id))
                    before=set(people.get(pid,{}).get('pending_fields',[]));after=set(person.get('pending_fields',[]))
                    for code in after-before:
                        conn.execute('INSERT INTO capture_pending_fields(intake_id,participant_id,field_code) VALUES(%s,%s,%s) ON CONFLICT(participant_id,field_code) DO NOTHING',(intake_id,pid,code))
                    for code in before-after:
                        conn.execute('DELETE FROM capture_pending_fields WHERE intake_id=%s AND participant_id=%s AND field_code=%s',(intake_id,pid,code))
                    for code, value in person['answers'].items():
                        if value != people.get(pid, {}).get('answers', {}).get(code):
                            conn.execute('INSERT INTO answers(intake_id,participant_id,field_code,value_json) VALUES(%s,%s,%s,%s)', (intake_id,pid,code,psycopg.types.json.Jsonb(value)))
                conn.execute('UPDATE intakes SET capture_version=capture_version+1,capture_active_id=%s,updated_at=now() WHERE id=%s', (active,intake_id))
                company=shareholders.applicant(updated)
                if company and any(company.get(key)!=people[company['id']].get(key) for key in ('shareholders_enabled','shareholders_complete','guarantors_complete')):
                    conn.execute('UPDATE intakes SET shareholders_enabled=%s,shareholders_complete=%s,guarantors_complete=%s WHERE id=%s',(bool(company.get('shareholders_enabled')),bool(company.get('shareholders_complete')),bool(company.get('guarantors_complete')),intake_id))
                conn.execute("UPDATE capture_turns SET status='complete',response_json=%s,audit_json=%s,updated_at=now() WHERE intake_id=%s AND request_id=%s", (psycopg.types.json.Jsonb(response),psycopg.types.json.Jsonb(audit),intake_id,body.request_id))
        return response
    except (ConversationUnavailable, InvalidProposal, HTTPException) as exc:
        with pool.connection() as conn:
            conn.execute("UPDATE capture_turns SET status='failed',updated_at=now() WHERE intake_id=%s AND request_id=%s AND status='processing'", (intake_id,body.request_id))
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, InvalidProposal):
            raise HTTPException(422, str(exc))
        logger.warning('Capture conversation failed: %s', exc)
        raise HTTPException(503, 'No pudimos procesar el mensaje; tu avance está guardado. Intenta otra vez')

@app.post('/intakes/{intake_id}/conversation/undo-last')
def undo_last_answer(intake_id: UUID, request: Request):
    user_id = owner(request)
    with pool.connection() as conn:
        with conn.transaction():
            intake_for_owner(conn, intake_id, user_id, editable=True)
            if not conversation_enabled_for(conn, user_id):
                raise HTTPException(404, 'Conversación no disponible')
            turn = conn.execute("SELECT * FROM capture_turns WHERE intake_id=%s AND status='complete' ORDER BY created_at DESC LIMIT 1", (intake_id,)).fetchone()
            actions = turn['audit_json'] if turn else []
            changes = [a for a in actions if a.get('type') != 'align_direct_answer']
            if len(changes) != 1 or changes[0].get('type') not in ('save_field','defer_field') or changes[0].get('field') == 'rfc':
                raise HTTPException(409, 'Solo puedes deshacer la última respuesta guardada en un campo')
            change = changes[0]
            table='capture_pending_fields' if change['type']=='defer_field' else 'answers'
            answer = conn.execute('SELECT updated_at FROM '+table+' WHERE intake_id=%s AND participant_id=%s AND field_code=%s FOR UPDATE', (intake_id,change['target_id'],change['field'])).fetchone()
            # Never remove a pre-existing answer that a model merely repeated.
            if not answer or answer['updated_at'] != turn['updated_at']:
                raise HTTPException(409, 'Ese dato ya existía o cambió después; no se deshizo')
            conn.execute('DELETE FROM '+table+' WHERE intake_id=%s AND participant_id=%s AND field_code=%s', (intake_id,change['target_id'],change['field']))
            people = conversation_people(conn, intake_id)
            active = active_person(people, change['target_id'])
            response = {'reply': resume_reply(people,active), 'active_id': active, 'authorization_links': [],'stage':capture_stage(people,active),'progress':capture_progress(people)}
            conn.execute('UPDATE intakes SET capture_version=capture_version+1,capture_active_id=%s,updated_at=now() WHERE id=%s', (active,intake_id))
            reversal = {'type':'undo_'+change['type'],'target_id':change['target_id'],'field':change['field'],'request_id':str(turn['request_id'])}
            conn.execute("INSERT INTO capture_turns(intake_id,request_id,user_message,status,response_json,audit_json) VALUES(%s,%s,%s,'complete',%s,%s)", (intake_id,uuid4(),'Deshacer última respuesta',psycopg.types.json.Jsonb(response),psycopg.types.json.Jsonb([reversal])))
    return response

install_document_routes(app,pool,owner,intake_for_owner,conversation_people,conversation_enabled_for,lambda intake_id:deliver_notice(pool,intake_id))

install_catalog_routes(app,pool,owner)

@app.post('/intakes/{intake_id}/shareholders/complete')
def complete_shareholders(intake_id:UUID,request:Request):
    """Equivalent explicit completion for the non-AI capture UI."""
    return complete_participant_list(intake_id,request,'listo accionistas')

@app.post('/intakes/{intake_id}/guarantors/complete')
def complete_guarantors(intake_id:UUID,request:Request):
    return complete_participant_list(intake_id,request,'listo avales')

def complete_participant_list(intake_id,request,message):
    uid=owner(request)
    with pool.connection() as conn:
        with conn.transaction():
            intake_for_owner(conn,intake_id,uid,editable=True)
            people=conversation_people(conn,intake_id)
            proposal=local_reference(people,message,None,[])
            if not proposal or not proposal['actions']:raise HTTPException(409,'Completa los datos antes de cerrar esta lista')
            try:updated,audit,active=apply_proposal(people,proposal,message)
            except InvalidProposal as exc:raise HTTPException(422,str(exc))
            company=shareholders.applicant(updated)
            conn.execute('UPDATE intakes SET shareholders_complete=%s,guarantors_complete=%s,capture_version=capture_version+1 WHERE id=%s',(bool(company.get('shareholders_complete')),bool(company.get('guarantors_complete')),intake_id))
            response={'reply':resume_reply(updated,active),'active_id':active,'stage':capture_stage(updated,active),'progress':capture_progress(updated),'authorization_links':[]}
            conn.execute("INSERT INTO capture_turns(intake_id,request_id,user_message,status,response_json,audit_json) VALUES(%s,%s,%s,'complete',%s,%s)",(intake_id,uuid4(),message,Jsonb(response),Jsonb(audit)))
    return response

from .followup import install as install_followup
install_followup(app,pool,owner,conversation_people,lambda intake_id:deliver_notice(pool,intake_id))

from .contact import install as install_contact
install_contact(app,pool,owner,digest,client_ip)
