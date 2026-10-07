"""Document requirements from the supplied Ana_Config_Documentos catalogue."""
import base64
import hashlib
import http.client
import json
import os
from urllib.parse import urlsplit

CATALOG = json.loads(os.environ.get('ANALEASE_DOCUMENT_CATALOG','[]'))
ROLES = {'solicitante':'Solicitante','aval':'Aval','representante':'RepresentanteLegal','accionista':'Accionista'}
MAX_BYTES = 10 * 1024 * 1024


class DocumentUnavailable(Exception):
    pass


def requirements(people, dependencies=None):
    from .conversation import person_hint
    dependencies = dependencies or {}
    result = []
    company_context = any(p['subject_type']=='PM' and p['role'] in ('solicitante','aval') for p in people.values())
    for pid, person in people.items():
        role = ROLES.get(person['role'])
        if person['role']=='representante' and not company_context:
            continue
        # PM on a representative catalogue row describes the company context,
        # not the representative's own PF RFC.
        scope = 'PM' if person['role'] == 'representante' else person['subject_type']
        for template in sorted(person.get('catalog',{}).get('documents',CATALOG), key=lambda row: row['order']):
            if template['role'] != role or template['scope'] != scope:
                continue
            field = template['dependency_field']
            value = person['answers'].get(field) or dependencies.get((pid,field)) if field else None
            applicable = None if field and not value else (not field or str(value).strip().casefold() == template['dependency_value'].casefold())
            from .catalog import question_for,field_rows
            field_spec=next((r for r in field_rows(person) or [] if r['code']==field),None)
            dependency_options=field_spec['options'] if field_spec else (['Casado','Soltero','Divorciado','Viudo','Unión libre'] if field=='estado_civil' else [])
            result.append({**template,'dependency_question':question_for(person,field) if field else None,'dependency_options':dependency_options,'participant_id':pid,'participant':person_hint(person),
                           'applicable':applicable})
    return result


def grouped_requirements(rows, people, states=()):
    """One checklist item per RFC/type/code, confined to this intake.

    Unknown conditions stay independent so grouping cannot bypass a role's
    unanswered dependency. Catalogue codes identify interchangeable documents.
    """
    import re
    state = {(str(s['participant_id']), s['document_code']): s for s in states}
    groups = {}
    for row in rows:
        person = people[row['participant_id']]
        rfc = (person.get('rfc') or '').strip().upper()
        valid = re.fullmatch(r'(?:[A-ZÑ&]{3}[0-9]{6}[A-Z0-9]{3}|[A-ZÑ&]{4}[0-9]{6}[A-Z0-9]{3})', rfc)
        identity = (person['subject_type'], rfc) if valid else ('participant', row['participant_id'])
        condition = row['participant_id'] if row['applicable'] is None else row['applicable']
        key = (identity, row['code'], condition)
        if key not in groups:
            groups[key] = {**row, 'required':False, 'members':[], 'reuse_sources':[]}
        group = groups[key]
        group['required'] |= row['required']
        current = state.get((row['participant_id'],row['code']), {})
        group['members'].append({'participant_id':row['participant_id'], 'code':row['code'],
                                 'participant':row['participant'], 'status':current.get('status','pending'),
                                 'upload_id':current.get('upload_id')})
    for group in groups.values():
        members = group['members']
        received = next((m for m in members if m['status']=='received'), None)
        group['status'] = ('not_applicable' if group['applicable'] is False else
                           'received' if group['applicable'] is True and received else
                           'deferred' if all(m['status']=='deferred' for m in members) else 'pending')
        group['upload_id'] = received['upload_id'] if received and group['status']=='received' else None
        group['participant'] = ' · '.join(dict.fromkeys(m['participant'] for m in members))
    return list(groups.values())


def required_missing(rows):
    return sum(1 for row in rows if row['required'] and row['applicable'] is not False
               and (row['applicable'] is not True or row['status']!='received'))


def upload_configured():
    p = urlsplit(os.environ.get('N8N_DOCUMENTS_WEBHOOK_URL',''))
    return (p.scheme == 'https' and p.netloc == 'flagent.app.n8n.cloud'
            and p.path == '/webhook/analease-documents' and not p.query and not p.fragment
            and bool(os.environ.get('N8N_DOCUMENTS_WEBHOOK_TOKEN')))


def inspect_file(content):
    if not content or len(content) > MAX_BYTES:
        raise ValueError('El archivo debe tener contenido y pesar como máximo 10 MB')
    if content.startswith(b'%PDF-'):
        return 'application/pdf','pdf'
    if content.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg','jpg'
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png','png'
    raise ValueError('Selecciona un archivo PDF, JPG o PNG')


def store_in_sharepoint(content, metadata):
    if not upload_configured():
        raise DocumentUnavailable('configuration')
    p = urlsplit(os.environ['N8N_DOCUMENTS_WEBHOOK_URL'])
    payload = json.dumps({'version':1,**metadata,'sha256':hashlib.sha256(content).hexdigest(),
                          'content_base64':base64.b64encode(content).decode('ascii')}).encode()
    conn = http.client.HTTPSConnection(p.hostname,timeout=60)
    try:
        conn.request('POST',p.path,body=payload,headers={'Content-Type':'application/json',
                     'X-AnaLease-Token':os.environ['N8N_DOCUMENTS_WEBHOOK_TOKEN']})
        response = conn.getresponse()
        raw = response.read(32769)
        if response.status != 200 or len(raw)>32768:
            raise DocumentUnavailable('http_or_size')
        result = json.loads(raw)
        if (not isinstance(result,dict) or result.get('ok') is not True or result.get('version') != 1
                or not isinstance(result.get('storage_id'),str) or not 1<=len(result['storage_id'])<=500):
            raise DocumentUnavailable('inconclusive')
        return result['storage_id']
    except (OSError,ValueError,http.client.HTTPException):
        raise DocumentUnavailable('transport_or_json')
    finally:
        conn.close()
