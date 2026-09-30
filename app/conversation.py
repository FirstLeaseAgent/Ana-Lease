"""Validated conversational proposals. No model can select a different intake."""
import copy
import http.client
import json
import os
import re
from urllib.parse import urlsplit
from uuid import uuid4

from .rules import FIELDS, masked_name, normalized_rfc

MAX_ACTIONS = 16
COMMON_FIELDS = {'nombre', 'correo_contacto', 'telefono', 'rfc'}
ORDER = {
    'solicitante': ['razon_social', 'nombre', 'nombre_comercial', 'actividad', 'pagina_web', 'correo_contacto', 'telefono'],
    'contacto': ['nombre', 'correo_contacto', 'telefono'],
    'aval': ['rfc', 'razon_social', 'nombre', 'ocupacion', 'pagina_web', 'correo_contacto', 'telefono'],
    'representante': ['rfc', 'nombre', 'cargo', 'correo_contacto', 'telefono'],
}
QUESTIONS = {
    'rfc': '¿Cuál es su RFC?', 'nombre': '¿Cuál es su nombre completo?',
    'razon_social': '¿Cuál es la razón social?', 'nombre_comercial': '¿Cuál es el nombre comercial?',
    'actividad': '¿A qué se dedica?', 'pagina_web': '¿Cuál es su página web?',
    'correo_contacto': '¿Cuál es el correo de contacto?', 'telefono': '¿Cuál es el teléfono?',
    'ocupacion': '¿Cuál es su ocupación?', 'cargo': '¿Cuál es su cargo?',
}


class ConversationUnavailable(Exception):
    """Only fixed, non-sensitive failure categories may appear in this exception."""


class InvalidProposal(ValueError):
    pass


def missing(person):
    fields = set(FIELDS[person['role']][person['subject_type']])
    if person['role'] in ('aval', 'representante') and not person.get('rfc'):
        fields.add('rfc')
    return [f for f in ORDER[person['role']] if f in fields and not (
        person.get('rfc') if f == 'rfc' else person['answers'].get(f))]


def active_person(people, preferred=None):
    if preferred in people and missing(people[preferred]):
        return preferred
    return next((pid for pid, p in people.items() if missing(p)), None)


def person_hint(person):
    name = person['answers'].get('nombre') or person['answers'].get('razon_social')
    label = f"{person['role'].capitalize()} ({person['subject_type']})"
    return label + (' · ' + masked_name(name) if isinstance(name, str) else '')


def context_for(people, preferred=None, session_email=None):
    active = active_person(people, preferred)
    context = {'version': 1, 'active_participant_id': active, 'participants': [
        {'id': pid, 'role': p['role'], 'subject_type': p['subject_type'], 'hint': person_hint(p),
         'known_fields': sorted(list(p['answers']) + (['rfc'] if p.get('rfc') else [])),
         'missing_fields': missing(p)} for pid, p in people.items()
    ]}
    if session_email:
        context['participants'].append({'id':'session_user','role':'usuario_verificado','subject_type':'PF',
            'hint':'Usuario que inició sesión · correo verificado',
            'known_fields':['correo_contacto'],'missing_fields':[]})
    return context


def context_for_turn(people, preferred, history, shown_question=None, session_email=None):
    canonical = resume_reply(people, preferred)
    question = shown_question or canonical
    last_reply = history[-1].get('assistant') if history else None
    if question not in (canonical, last_reply):
        raise InvalidProposal('La pregunta cambió; recarga para continuar')
    context = context_for(people, preferred, session_email)
    active = context['active_participant_id']
    field = missing(people[active])[0] if active and question == canonical else None
    context['current_question'] = {'text': question, 'participant_id': active, 'field': field}
    options = []
    if active and field in COMMON_FIELDS:
        target = people[active]
        for source_id, source in people.items():
            if source_id == active:
                continue
            known = bool(source.get('rfc')) if field == 'rfc' else bool(source['answers'].get(field))
            compatible = not (field == 'nombre' and source['subject_type'] != 'PF')
            if field == 'rfc' and target['role'] == 'representante':
                compatible = source['subject_type'] == 'PF'
            if known and compatible:
                options.append({'type':'reuse_field','target_id':active,'source_id':source_id,
                                'source_role':source['role'],'field':field})
        if session_email and field == 'correo_contacto':
            options.append({'type':'reuse_field','target_id':active,'source_id':'session_user',
                            'source_role':'usuario_verificado','field':field})
    context['current_question']['reuse_options'] = options
    recent_ids = []
    for turn in reversed(history):
        for target in reversed(turn.get('capture_targets', [])):
            pid = target.get('id')
            if pid in people and pid not in recent_ids:
                recent_ids.append(pid)
    context['recent_capture_participant_ids'] = recent_ids
    # Resuming a request shows a server question, not necessarily the last old turn.
    recent = list(history[-3:])
    if not recent or recent[-1].get('assistant') != question:
        recent.append({'user': '', 'assistant': question})
    return context, recent


def history_for_turns(rows, people):
    history = []
    for row in reversed(rows):
        targets = {}
        for action in row.get('audit_json') or []:
            if action.get('type') not in ('save_field','reuse_field','add_participant'):
                continue
            pid = action.get('target_id')
            if pid in people:
                target = targets.setdefault(pid, {'id':pid,'role':people[pid]['role'],'fields':[]})
                if action.get('field') and action['field'] not in target['fields']:
                    target['fields'].append(action['field'])
        history.append({'user':row['user_message'],'assistant':row['response_json']['reply'],
                        'capture_targets':list(targets.values())})
    return history


def align_direct_answer(proposal, people, message, preferred, question):
    """Tie a scalar answer to the verified question the browser actually showed.

    Explicit field instructions and multi-field proposals remain model decisions.
    """
    if question != resume_reply(people, preferred) or not isinstance(proposal, dict):
        return proposal, None
    active = active_person(people, preferred)
    actions = proposal.get('actions')
    if not active or not isinstance(actions, list) or len(actions) != 1:
        return proposal, None
    action = actions[0]
    if not isinstance(action, dict) or action.get('type') != 'save_field' or action.get('source_id') is not None:
        return proposal, None
    if not isinstance(action.get('value'), str) or action['value'].strip() != message.strip():
        return proposal, None
    if re.search(r'\b(rfc|correo|email|tel[eé]fono|p[aá]gina|web|nombre|raz[oó]n|comercial|actividad|cargo|ocupaci[oó]n|aval|representante|contacto|mismo|corrige|cambia)\b', message, re.I):
        return proposal, None
    expected = missing(people[active])[0]
    if action.get('target_id') == active and action.get('field') == expected:
        return proposal, None
    aligned = copy.deepcopy(proposal)
    aligned['actions'][0]['target_id'] = active
    aligned['actions'][0]['field'] = expected
    return aligned, {'type': 'align_direct_answer', 'target_id': active,
                     'proposed_field': action.get('field'), 'field': expected}


def resume_reply(people, preferred=None):
    active = active_person(people, preferred)
    if not active:
        applicant = next((p for p in people.values() if p['role']=='solicitante'),None)
        if applicant:
            if applicant['subject_type']=='PM' and not any(p['role']=='representante' for p in people.values()):
                return 'Los datos están guardados. Para continuar, agrega al representante legal de la empresa. También puedes agregar un aval.'
            return 'Los datos están guardados. Continuemos con los documentos de esta solicitud.'
        return 'Este avance está guardado. Puedes agregar un aval o representante, o regresar después.'
    person = people[active]
    return f'Estamos completando la información de {person_hint(person)}. {QUESTIONS[missing(person)[0]]}'


def capture_stage(people, preferred=None):
    applicant = next((p for p in people.values() if p['role']=='solicitante'),None)
    if not applicant or active_person(people,preferred):
        return 'capture'
    if applicant['subject_type']=='PM' and not any(p['role']=='representante' for p in people.values()):
        return 'capture'
    return 'documents'


def redact_reply(reply, people):
    # Browser receives context hints, not a replay of previously stored answers.
    for person in people.values():
        values = list(person['answers'].values()) + [person.get('rfc')]
        for value in values:
            if isinstance(value, str) and len(value) >= 4 and value in reply:
                hint = masked_name(value) if ' ' in value else '••••'
                reply = reply.replace(value, hint)
    return reply


def reply_after_proposal(proposal, people, audit, preferred=None):
    # The model interprets intent; committed fields determine the next question.
    # Keep free-form clarifications when no capture action was accepted.
    if audit:
        return resume_reply(people, preferred)
    return redact_reply(proposal['reply'], people)


def apply_proposal(people, proposal, message, preferred=None, session_email=None):
    """Return a full validated snapshot and audit trail; caller commits atomically."""
    if not isinstance(proposal, dict) or set(proposal) != {'reply', 'actions'}:
        raise InvalidProposal('Respuesta de IA no reconocida')
    if not isinstance(proposal['reply'], str) or not 1 <= len(proposal['reply']) <= 1800:
        raise InvalidProposal('Respuesta de IA no reconocida')
    actions = proposal['actions']
    if not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
        raise InvalidProposal('Demasiados cambios propuestos')
    result = copy.deepcopy(people)
    audit = []
    new_id = None
    preferred_id = preferred
    for action in actions:
        if not isinstance(action, dict) or set(action) != {'type', 'target_id', 'source_id', 'role', 'field', 'value', 'evidence'}:
            raise InvalidProposal('Acción no reconocida')
        if not isinstance(action['target_id'], str) or any(
            action[k] is not None and not isinstance(action[k], str)
            for k in ('source_id', 'role', 'field', 'value')
        ):
            raise InvalidProposal('Acción no reconocida')
        evidence = action['evidence']
        if not isinstance(evidence, str) or not evidence.strip() or evidence.casefold() not in message.casefold():
            raise InvalidProposal('El cambio no está respaldado por tu mensaje')
        kind = action['type']
        if kind == 'focus_participant':
            if action['target_id'] not in people or any(action[k] is not None for k in ('source_id','role','field','value')):
                raise InvalidProposal('El participante no pertenece a esta solicitud')
            preferred_id = action['target_id']
            audit.append({'type': kind, 'target_id': preferred_id, 'evidence': evidence})
            continue
        if kind == 'add_participant':
            role = action['role']
            if role not in ('aval', 'representante') or role not in message.casefold() or new_id:
                raise InvalidProposal('Revisa qué participante deseas agregar')
            if action['target_id'] != 'new' or any(action[k] is not None for k in ('source_id', 'field', 'value')):
                raise InvalidProposal('Acción no reconocida')
            if role == 'aval' and sum(p['role'] == 'aval' for p in result.values()) >= 3:
                raise InvalidProposal('Máximo tres avales')
            new_id = str(uuid4())
            result[new_id] = {'id': new_id, 'role': role, 'subject_type': 'PF', 'rfc': None, 'answers': {}}
            audit.append({'type': kind, 'target_id': new_id, 'role': role, 'evidence': evidence})
            preferred_id = new_id
            continue
        if kind not in ('save_field', 'reuse_field') or action['role'] is not None:
            raise InvalidProposal('Acción no reconocida')
        target_id = new_id if action['target_id'] == 'new' else action['target_id']
        if target_id not in result:
            raise InvalidProposal('El participante no pertenece a esta solicitud')
        target = result[target_id]
        field = action['field']
        if kind == 'reuse_field':
            source_id = action['source_id']
            if field not in COMMON_FIELDS or action['value'] is not None:
                raise InvalidProposal('El origen no pertenece a esta solicitud o el campo no se puede reutilizar')
            if source_id == 'session_user':
                if field != 'correo_contacto' or not session_email:
                    raise InvalidProposal('Solo se puede reutilizar el correo verificado de tu sesión')
                value = session_email
            else:
                if source_id not in people:
                    raise InvalidProposal('El origen no pertenece a esta solicitud')
                source = result[source_id]
                # Explicitly shared contact channels do not imply the same identity.
                if field == 'nombre' and source['subject_type'] != 'PF':
                    raise InvalidProposal('La razón social no es el nombre de una persona')
                value = source.get('rfc') if field == 'rfc' else source['answers'].get(field)
            if not value:
                raise InvalidProposal('Ese dato todavía no está capturado')
        else:
            if action['source_id'] is not None:
                raise InvalidProposal('Acción no reconocida')
            value = action['value']
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 500 or value.strip().casefold() not in evidence.casefold():
                raise InvalidProposal('El dato no aparece en tu mensaje')
            value = value.strip()
            source_id = None
        if field == 'rfc':
            if target['role'] not in ('aval', 'representante'):
                raise InvalidProposal('El RFC de este participante no se puede cambiar aquí')
            try:
                value, subject = normalized_rfc(value)
            except ValueError:
                raise InvalidProposal('Revisa el formato del RFC')
            if target['role'] == 'representante' and subject != 'PF':
                raise InvalidProposal('El RFC del representante debe corresponder a persona física')
            if any(f not in FIELDS[target['role']][subject] for f in target['answers']):
                raise InvalidProposal('El RFC no corresponde al tipo de datos de este participante')
            existing = target.get('rfc')
            target['subject_type'] = subject
        else:
            if field not in FIELDS[target['role']][target['subject_type']]:
                raise InvalidProposal('El campo no corresponde a ese participante')
            existing = target['answers'].get(field)
        if existing and existing != value:
            raise InvalidProposal('Ese campo ya tiene otro valor; confirma la corrección con nuestro equipo')
        # Equal values are idempotent; a repeated reference must not duplicate data.
        if field == 'rfc':
            target['rfc'] = value
        else:
            target['answers'][field] = value
        audit.append({'type': kind, 'target_id': target_id, 'source_id': source_id,
                      'field': field, 'evidence': evidence})
        preferred_id = target_id
    if new_id and result[new_id].get('rfc') and any(
        p['role'] == result[new_id]['role'] and p.get('rfc') == result[new_id]['rfc']
        for pid, p in result.items() if pid != new_id
    ):
        raise InvalidProposal('Ese participante ya está capturado; utiliza sus datos existentes')
    return result, audit, active_person(result, preferred_id)


def call_agent(message, context, history):
    parts = urlsplit(os.environ.get('N8N_CONVERSATION_WEBHOOK_URL', ''))
    token = os.environ.get('N8N_CONVERSATION_WEBHOOK_TOKEN', '')
    if (parts.scheme != 'https' or parts.netloc != 'flagent.app.n8n.cloud'
            or parts.path != '/webhook/analease-conversation' or parts.query or parts.fragment or not token):
        raise ConversationUnavailable('configuration')
    payload = json.dumps({'version': 1, 'message': message, 'context': context, 'history': history}).encode()
    conn = http.client.HTTPSConnection(parts.hostname, timeout=45)
    try:
        conn.request('POST', parts.path, body=payload, headers={'Content-Type': 'application/json', 'X-AnaLease-Token': token})
        response = conn.getresponse()
        if response.status != 200:
            raise ConversationUnavailable(f'http_{response.status}')
        raw = response.read(32769)
        if len(raw) > 32768:
            raise ConversationUnavailable('oversized')
        data = json.loads(raw)
    except (OSError, ValueError, http.client.HTTPException):
        raise ConversationUnavailable('transport_or_json')
    finally:
        conn.close()
    if not isinstance(data, dict) or data.get('ok') is not True or data.get('version') != 1:
        raise ConversationUnavailable('inconclusive')
    return data.get('proposal')
