"""Validated conversational proposals. No model can select a different intake."""
import copy
import http.client
import json
import os
import re
from urllib.parse import urlsplit
from uuid import uuid4

from .rules import FIELDS, masked_name, normalized_rfc
from .catalog import fields_for, field_rows, question_for, reuse_source, validate_value, baseline
from .memory import model_memory, relations_from_actions, identity_members
from . import shareholders
from . import handoffs

MAX_ACTIONS = 16
COMMON_FIELDS = {'nombre', 'correo_contacto', 'telefono', 'rfc', 'curp'}
ORDER = {
    'solicitante': ['razon_social', 'nombre', 'nombre_comercial', 'actividad', 'pagina_web', 'correo_contacto', 'telefono'],
    'contacto': ['nombre', 'correo_contacto', 'telefono'],
    'aval': ['rfc', 'razon_social', 'nombre', 'ocupacion', 'pagina_web', 'correo_contacto', 'telefono'],
    'representante': ['rfc', 'nombre', 'cargo', 'correo_contacto', 'telefono'],
    'accionista': ['rfc','nombre','razon_social','curp','porcentaje_participacion'],
}
QUESTIONS = {
    'rfc': '¿Cuál es su RFC?', 'nombre': '¿Cuál es su nombre completo?',
    'razon_social': '¿Cuál es la razón social?', 'nombre_comercial': '¿Cuál es el nombre comercial?',
    'actividad': '¿A qué se dedica?', 'pagina_web': '¿Cuál es su página web?',
    'correo_contacto': '¿Cuál es el correo de contacto?', 'telefono': '¿Cuál es el teléfono?',
    'ocupacion': '¿Cuál es su ocupación?', 'cargo': '¿Cuál es su cargo?',
    'curp': '¿Cuál es su CURP?',
    'porcentaje_participacion': '¿Cuál es su porcentaje de participación en la empresa? Debe ser mayor al 10%.',
}


class ConversationUnavailable(Exception):
    """Only fixed, non-sensitive failure categories may appear in this exception."""


class InvalidProposal(ValueError):
    pass


def missing(person):
    fields = fields_for(person)
    if person['role'] in ('aval', 'representante', 'accionista') and not person.get('rfc'):
        fields.add('rfc')
    rows=field_rows(person)
    order=((['rfc'] if person['role'] in ('aval','representante','accionista') else [])+[r['code'] for r in rows]) if rows is not None else ORDER[person['role']]
    return [f for f in order if f in fields and not (
        person.get('rfc') if f == 'rfc' else person['answers'].get(f))]


def active_person(people, preferred=None):
    company=shareholders.applicant(people)
    if company and company.get('shareholders_enabled'):
        for roles in [('solicitante','contacto'),('representante',),('accionista',),('aval',)]:
            if roles==('representante',) and company['subject_type']=='PM' and not any(p['role']=='representante' for p in people.values()):return None
            if roles==('aval',) and shareholders.needs_completion(people):return None
            pending=[pid for pid,p in people.items() if p['role'] in roles and missing(p)]
            if pending:return preferred if preferred in pending else pending[0]
        return None
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
    context['capture_schema']=[{'participant_id':pid,'fields':[{'code':r['code'],'question':r['question'],'type':r['type'],'options':r['options']} for r in field_rows(p if p.get('catalog') is not None else dict(p,catalog=baseline())) or []]} for pid,p in people.items()]
    if session_email:
        context['participants'].append({'id':'session_user','role':'usuario_verificado','subject_type':'PF',
            'hint':'Usuario que inició sesión · correo verificado',
            'known_fields':['correo_contacto'],'missing_fields':[]})
    return context


def context_for_turn(people, preferred, history, shown_question=None, session_email=None, relations=None):
    canonical = resume_reply(people, preferred)
    question = shown_question or canonical
    last_reply = history[-1].get('assistant') if history else None
    if question not in (canonical, last_reply):
        raise InvalidProposal('La pregunta cambió; recarga para continuar')
    context = context_for(people, preferred, session_email)
    active = context['active_participant_id']
    stage=capture_stage(people,preferred)
    company=shareholders.applicant(people)
    pending_role=('representante' if pending_representative(people,preferred) else
                  {'shareholders':'accionista','guarantors':'aval'}.get(stage))
    context['capture_stage']=stage
    context['participant_transition']={'pending_role':pending_role,'company_id':company['id'] if company else None,
        'can_finish':not active and stage in ('shareholders','guarantors'),
        'new_participant_schema':{subject:[{'code':r['code'],'question':r['question'],'type':r['type'],'options':r['options']} for r in field_rows({
            'role':pending_role,'subject_type':subject,'catalog':(company.get('catalog') if company else None) or baseline()
        }) or []] for subject in (('PF',) if pending_role=='representante' else ('PF','PM'))} if pending_role else {}}
    field = missing(people[active])[0] if active else None
    context['current_question'] = {'text': question, 'participant_id': active, 'field': field}
    options = []
    if active and field in COMMON_FIELDS:
        target = people[active]
        for source_id, source in people.items():
            if source_id == active:
                continue
            known = bool(source.get('rfc')) if field == 'rfc' else bool(source['answers'].get(field))
            compatible = not (field in ('nombre','curp') and source['subject_type'] != 'PF')
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
    context['conversation_memory'] = model_memory(relations or [], history)
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
    if not isinstance(proposal, dict):
        return proposal, None
    active = active_person(people, preferred)
    actions = proposal.get('actions')
    # A repeated role question must not discard a plain answer to that field.
    # Preserve questions, uncertainty and identity references as model decisions.
    if active and actions == [] and missing(people[active])[0] in ('cargo','ocupacion'):
        field=missing(people[active])[0]
        value = message.strip()
        repeated = question_for(people[active], field) in proposal.get('reply', '')
        if repeated and handoffs.role_answer(value):
            aligned = copy.deepcopy(proposal)
            aligned['actions'] = [{'type':'save_field','target_id':active,'source_id':None,
                                  'role':None,'field':field,'value':value,'evidence':value}]
            return aligned, {'type':'align_direct_answer','target_id':active,
                             'proposed_field':None,'field':field}
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


def identity_reference(message):
    """Resolve explicit role references; field-only sharing is not identity."""
    if any(mark in message for mark in ('?', '¿')):
        return None
    text=' '.join(message.casefold().strip(' .!').split())
    text=re.sub(r'^soy yo[,; ]+', '', text)
    roles=r'(?:solicitante|contacto|aval|accionista|representante(?: legal)?)'
    create=re.match(r'(?:(?:quiero|vamos a) )?(?:agrega|agregar|añade|añadir) '
                    r'(?:(?:al|a la|un|una|otro|otra|el|la) )?('+roles+r')[,;:]?\s*',text)
    created_role=create.group(1).replace(' legal','') if create else None
    if create:
        text=text[create.end():]
    source=r'(?P<source>'+roles+r')(?: (?P<source_index>[1-3]))?'
    target=r'(?P<target>'+roles+r')(?: (?P<target_index>[1-3]))?'
    patterns=[
        r'(?:(?:el |la )?'+target+r' )?(?:es|soy) (?:el mismo|la misma)(?: que)? (?:el |la )?'+source,
        r'(?:los )?datos (?:(?:del|de la) '+target+r' )?son (?:los mismos|iguales)'
        r'(?: que| a)? (?:(?:los )?(?:datos )?(?:del|de la)|el|la) '+source,
    ]
    match=next((m for pattern in patterns if (m:=re.fullmatch(pattern,text))),None)
    if not match:
        return None
    target_role=(match.group('target') or created_role)
    target_role=target_role.replace(' legal','') if target_role else None
    if created_role and target_role!=created_role:
        return None
    return {'target_role':target_role,'target_index':match.group('target_index'),
            'source_role':match.group('source').replace(' legal',''),
            'source_index':match.group('source_index'),'create':bool(create)}


def source_for_reference(people, reference):
    candidates=[pid for pid,p in people.items() if p['role']==reference['source_role']]
    index=reference['source_index']
    if index:
        return candidates[int(index)-1] if int(index)<=len(candidates) else None
    return candidates[0] if len(candidates)==1 else None


def pending_representative(people,preferred=None):
    company=shareholders.applicant(people)
    return bool(company and company['subject_type']=='PM' and not active_person(people,preferred)
                and not any(p['role']=='representante' for p in people.values()))


def named_representative_reference(people,message,relations=None,role='representante'):
    matches=handoffs.matching_names(people,message)
    if not matches:return None
    first=matches[0]
    if any(pid not in identity_members(people,relations or [],first) for pid in matches):
        return {'ambiguous':True}
    source=people[first];ids=[pid for pid,p in people.items() if p['role']==source['role']]
    return {'target_role':role,'target_index':None,'source_role':source['role'],
            'source_index':str(ids.index(first)+1),'create':True}


def implicit_representative_input(people,message):
    if not pending_representative(people):return False
    reference=identity_reference(message)
    if reference and reference['target_role'] is None:
        source=source_for_reference(people,reference)
        return bool(source and people[source]['subject_type']=='PF')
    return bool(handoffs.matching_names(people,message) or handoffs.literal_name(message) or handoffs.pf_rfc(message))


def implicit_list_input(people,message,preferred=None):
    if active_person(people,preferred) or shareholders.completion_evidence(message):return False
    if capture_stage(people,preferred) not in ('shareholders','guarantors'):return False
    if handoffs.literal_name(message):return True
    try:normalized_rfc(message)
    except ValueError:return False
    return True


def identity_audit(people, audit, message):
    """Persist an explicit identity statement only after validated data reuse."""
    reference=identity_reference(message)
    declaration=shareholders.identity_declaration(message)
    if not reference and declaration:
        reference=identity_reference(declaration['identity'])
        if reference:reference=dict(reference,target_role='accionista')
    if not reference and not any(mark in message for mark in ('?','¿')):
        clause=re.search(r'\b(?:es|soy) (?:el mismo|la misma)(?: que)? (?:el |la )?(?:contacto|representante(?: legal)?|aval|accionista)(?: [1-3])?\b',message,re.I)
        if clause and not re.search(r'\b(no|si|correo|tel[eé]fono|email)\b',message[:clause.start()],re.I):
            reference=identity_reference(clause.group(0))
    if not reference:
        # The question asks who will act as representative; an exact stored full name
        # identifies that source, without changing the scope of later name answers.
        matches=handoffs.matching_names(people,message)
        reused={a.get('source_id') for a in audit if a.get('type')=='reuse_field' and a.get('field')=='nombre'
                and a.get('target_id') in people and people[a['target_id']]['role'] in ('representante','accionista','aval')}
        sources=[pid for pid in matches if pid in reused]
        if len(sources)==1:
            source=sources[0];ids=[pid for pid,p in people.items() if p['role']==people[source]['role']]
            target_roles={people[a['target_id']]['role'] for a in audit if a.get('type')=='reuse_field' and a.get('field')=='nombre' and a.get('source_id')==source and a.get('target_id') in people}
            if len(target_roles)==1:
                reference={'target_role':next(iter(target_roles)),'source_role':people[source]['role'],'source_index':str(ids.index(source)+1)}
    if not reference:
        return []
    source=source_for_reference(people,reference)
    candidates={ (a['target_id'],source) for a in audit
                 if a.get('type')=='reuse_field' and source in people
                 and a.get('target_id') in people
                 and a['target_id']!=source
                 and (not reference['target_role'] or people[a['target_id']]['role']==reference['target_role']) }
    candidates.update((a['target_id'],source) for a in audit
                      if a.get('type')=='focus_participant' and a.get('target_id') in people
                      and source in people and a['target_id']!=source
                      and (not reference['target_role'] or people[a['target_id']]['role']==reference['target_role']))
    if len(candidates)!=1:
        return []
    target,source=next(iter(candidates))
    event={'type':'confirm_identity','target_id':target,'source_id':source,'evidence':message}
    return [event] if relations_from_actions(people,[event]) else []


def local_reference(people,message,preferred,history,relations=None):
    active=active_person(people,preferred)
    stage=capture_stage(people,preferred)
    list_role={'shareholders':'accionista','guarantors':'aval'}.get(stage) if not active else None
    if list_role and shareholders.list_finished(message):
        return shareholders.local_proposal(people,message,active,missing,stage)
    declaration=shareholders.identity_declaration(message) if stage=='shareholders' or (active and people[active]['role']=='accionista') else None
    reference=identity_reference(message)
    if list_role and not reference and not declaration:
        reference=named_representative_reference(people,message,relations,list_role)
        if reference and reference.get('ambiguous'):
            return {'reply':'Ese nombre coincide con más de un participante. Indica de qué rol deseas tomar sus datos.','actions':[]}
    if declaration:
        reference=identity_reference(declaration['identity'])
        reference=dict(reference,target_role='accionista',create=not active)
    if not active and stage in ('shareholders','guarantors') and reference and reference['target_role'] is None:
        reference=dict(reference,target_role='accionista' if stage=='shareholders' else 'aval',create=True)
    pending=pending_representative(people,preferred)
    if pending and not reference:
        reference=named_representative_reference(people,message,relations)
        if reference and reference.get('ambiguous'):
            return {'reply':'Ese nombre coincide con más de un participante. Indica el rol de origen, por ejemplo «es el mismo contacto».','actions':[]}
    if pending and reference and reference['target_role'] is None:
        reference=dict(reference,target_role='representante',create=True)
    if reference:
        source_id=source_for_reference(people,reference)
        if not source_id:
            return {'reply':'Indica cuál '+reference['source_role']+' deseas utilizar; si hay varios, indica su número.','actions':[]}
        source=people[source_id]
        if source['subject_type']!='PF':
            return {'reply':'Ese participante es una persona moral. Indica una persona física para reutilizar su identidad.','actions':[]}
        role=reference['target_role'] or (people[active]['role'] if active else None)
        source_group=identity_members(people,relations or [],source_id)
        source_rfcs={people[pid]['rfc'] for pid in source_group if people[pid].get('rfc')}
        known_rfc=next(iter(source_rfcs)) if len(source_rfcs)==1 else None
        if role==source['role']:
            return {'reply':'Ese participante ya está registrado. Indica el otro rol que deseas completar.','actions':[]}
        targets=[pid for pid,p in people.items() if p['role']==role]
        target_index=reference['target_index']
        target_id=None
        if target_index:
            target_id=targets[int(target_index)-1] if int(target_index)<=len(targets) else None
            if not target_id:
                return {'reply':'No encontramos ese participante en esta solicitud. Indica a quién te refieres.','actions':[]}
        elif not reference['create']:
            target_id=active if active in targets else (targets[0] if len(targets)==1 else None)
            if len(targets)>1 and not target_id:
                return {'reply':'Indica cuál '+str(role)+' deseas completar.','actions':[]}
        if reference['create'] or not targets:
            # An existing same-role RFC is focused instead of creating a duplicate.
            existing=[pid for pid in targets if known_rfc and people[pid].get('rfc')==known_rfc]
            if len(existing)==1:
                target_id=existing[0]
        if target_id==source_id:
            return {'reply':'Ese participante ya está registrado. Indica el otro rol que deseas completar.','actions':[]}
        actions=[]
        if target_id is None:
            if role not in ('aval','representante','accionista'):
                return None
            target_id='new'
            person={'role':role,'subject_type':'PF','rfc':None,'answers':{}}
            if source.get('catalog') is not None:person['catalog']=source['catalog']
            actions.append({'type':'add_participant','target_id':'new','source_id':None,
                            'role':role,'field':None,'value':None,'evidence':message})
        else:
            person=people[target_id]
        if person['subject_type']!='PF':
            return {'reply':'El participante de destino es una persona moral; no podemos reutilizar una identidad PF para ese rol.','actions':[]}
        shared=('rfc','nombre','correo_contacto','telefono','curp')
        def value(p,f):return p.get('rfc') if f=='rfc' else p['answers'].get(f)
        target_group=identity_members(people,relations or [],target_id) if target_id in people else set()
        group=source_group | target_group
        if any(len({value(people[pid],f) for pid in group if value(people[pid],f)} |
                   ({value(person,f)} if value(person,f) else set()))>1 for f in shared):
            return {'reply':'Los datos capturados entran en conflicto. Confirma la corrección con nuestro equipo antes de reutilizarlos.','actions':[]}
        allowed=fields_for(person) | ({'rfc'} if role in ('aval','representante','accionista') else set())
        for f in shared:
            if f not in allowed or value(person,f):continue
            origins=[pid for pid in people if pid in source_group and value(people[pid],f)]
            origin=source_id if source_id in origins else (origins[0] if origins else None)
            if origin:
                actions.append({'type':'reuse_field','target_id':target_id,'source_id':origin,
                                'role':None,'field':f,'value':None,'evidence':message})
        if not actions:
            actions=[{'type':'focus_participant','target_id':target_id,'source_id':None,
                      'role':None,'field':None,'value':None,'evidence':message}]
        if declaration:
            actions.append(shareholders.action('save_field',target=target_id,
                field='porcentaje_participacion',value=declaration['percentage'],message=message))
        return {'reply':'Usaré los datos disponibles de la misma persona.','actions':actions}
    if not active:
        if list_role and implicit_list_input(people,message,preferred):
            try:normalized_rfc(message);field='rfc'
            except ValueError:field='nombre'
            return {'reply':'Vamos a completar a esta persona.','actions':[
                shareholders.action('add_participant',role=list_role,message=message),
                shareholders.action('save_field',field=field,value=message.strip(),message=message)]}
        if pending:
            if handoffs.literal_name(message) or handoffs.pf_rfc(message):
                field='rfc' if handoffs.pf_rfc(message) else 'nombre'
                return {'reply':'Vamos a completar al representante legal.','actions':[
                    shareholders.action('add_participant',role='representante',message=message),
                    shareholders.action('save_field',field=field,value=message.strip(),message=message)]}
            if message.strip(' .!').casefold() in ('ok','sí','si','listo','continuemos','adelante','representante','representante legal'):
                if message.strip(' .!').casefold().startswith('representante'):
                    return {'reply':'Vamos a completar al representante legal.','actions':[
                        shareholders.action('add_participant',role='representante',message=message)]}
                return {'reply':resume_reply(people,preferred),'actions':[]}
        return shareholders.local_proposal(people,message,active,missing,capture_stage(people,preferred))
    person=people[active]
    field=missing(person)[0]
    channel=handoffs.channel_reference(people,relations,history,active,field,message)
    if channel:return channel
    if person['role'] in ('accionista','aval') and shareholders.list_finished(message):
        return {'reply':'Antes de cerrar la lista, falta completar a esta persona. '+question_for(person,field),'actions':[]}
    if field in ('cargo','ocupacion') and handoffs.role_answer(message):
        return {'reply':'Dato guardado.','actions':[
            shareholders.action('save_field',target=active,field=field,value=message.strip(),message=message)]}
    if field in COMMON_FIELDS and re.fullmatch(r'(?:es |son )?(?:el |la |los )?(?:mismo|misma|mismos|igual)',message.strip(' .!'),re.I):
        linked=identity_members(people,relations or [],active)
        candidates=[pid for pid in people if pid in linked and pid!=active and
                    (people[pid].get('rfc') if field=='rfc' else people[pid]['answers'].get(field))]
        values={people[pid].get('rfc') if field=='rfc' else people[pid]['answers'][field] for pid in candidates}
        if len(values)==1:
            return {'reply':'Usaré el dato de la misma persona ya confirmada.','actions':[
                {'type':'reuse_field','target_id':active,'source_id':candidates[0],
                 'role':None,'field':field,'value':None,'evidence':message}]}
    source_field=reuse_source(person,field)
    if source_field and person['answers'].get(source_field):
        normalized=message.casefold().replace('ó','o').strip(' .!?')
        if re.search(r'\b(no|diferente|distint[oa]|otr[oa])\b',normalized):return None
        source_label=source_field.replace('_',' ')
        direct=bool(re.search(r'\b(mism[oa]|igual|iguak|igua)\b',normalized) and source_label in normalized)
        short=bool(re.fullmatch(r'(?:s[ií][, ]+)?(?:es\s+)?(?:el\s+|la\s+)?(?:igual|iguak|igua|mismo|misma)',normalized))
        confirmed=normalized==person['role'] and any(source_label in t.get('user','').casefold().replace('ó','o') for t in history[-4:])
        if direct or short or confirmed:
            return {'reply':'Usaré el dato ya registrado.','actions':[{'type':'reuse_field','target_id':active,'source_id':active,'role':None,'field':field,'value':None,'evidence':message}]}
    if re.fullmatch(r'(?:ok[, ]*)?(?:continuemos|contin[uú]a|seguir|adelante)',message.strip(),re.I):
        return {'reply':resume_reply(people,preferred),'actions':[]}
    return shareholders.local_proposal(people,message,active,missing,capture_stage(people,preferred))


def capture_progress(people):
    total=sum(len(fields_for(p))+(p['role']!='contacto') for p in people.values())
    completed=sum(sum(bool(p['answers'].get(f)) for f in fields_for(p))+bool(p.get('rfc')) for p in people.values())
    return {'completed':completed,'total':total,'label':'Captura de datos'}


def resume_reply(people, preferred=None):
    active = active_person(people, preferred)
    if not active:
        applicant = next((p for p in people.values() if p['role']=='solicitante'),None)
        if applicant:
            if applicant['subject_type']=='PM' and not any(p['role']=='representante' for p in people.values()):
                return 'Los datos están guardados. ¿Quién es el representante legal de la empresa? Escribe su nombre completo o «es el mismo contacto».'
            if shareholders.needs_completion(people):
                if any(p['role']=='accionista' for p in people.values()):
                    return '¿Hay otro accionista con más del 10% de participación? Puedes agregarlo o responder «no hay más».'
                return '¿Quién tiene más del 10% de participación en la empresa? Si ya lo registramos, puedes decir, por ejemplo, «es el mismo contacto y tiene el 60%». Si no hay ninguno, indícalo.'
            if applicant.get('shareholders_enabled') and not applicant.get('guarantors_complete'):
                if any(p['role']=='aval' for p in people.values()):
                    return '¿Agregarás otro aval? Puedes indicar quién es o responder «no hay más».'
                return '¿Quién será el aval de la solicitud? Puede ser una persona ya registrada. Si no agregarás uno, responde «sin aval».'
            return 'Los datos están guardados. Continuemos con los documentos de esta solicitud.'
        return 'Este avance está guardado. Puedes agregar un aval o representante, o regresar después.'
    person = people[active]
    label='Representante legal' if person['role']=='representante' else person['role'].capitalize()
    peers=[pid for pid,p in people.items() if p['role']==person['role']]
    if len(peers)>1:label+=' '+str(peers.index(active)+1)
    return f'{label}: {question_for(person,missing(person)[0])}'


def capture_stage(people, preferred=None):
    applicant = next((p for p in people.values() if p['role']=='solicitante'),None)
    if not applicant or active_person(people,preferred):
        return 'capture'
    if applicant['subject_type']=='PM' and not any(p['role']=='representante' for p in people.values()):
        return 'capture'
    if shareholders.needs_completion(people):return 'shareholders'
    if applicant.get('shareholders_enabled') and not applicant.get('guarantors_complete'):return 'guarantors'
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
    active=active_person(people,preferred)
    if active:
        expected=missing(people[active])[0]
        # A model clarification may not skip to a different capture question
        # when no field was committed. Genuine clarification text is preserved.
        for person in people.values():
            for field in fields_for(person) | {'rfc'}:
                if question_for(person,field) in proposal['reply'] and (
                        person['id']!=active or field!=expected):
                    return resume_reply(people,preferred)
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
        if kind in ('finish_shareholders','finish_guarantors'):
            company=shareholders.applicant(result)
            if (any(action[k] is not None for k in ('source_id','role','field','value')) or not company or
                action['target_id']!=company['id'] or active_person(result,preferred_id) or
                capture_stage(result,preferred_id)!=('shareholders' if kind=='finish_shareholders' else 'guarantors')):
                raise InvalidProposal('Completa los datos antes de cerrar la lista de participantes')
            expected=shareholders.local_proposal(result,message,None,missing,capture_stage(result,preferred_id))
            if not (expected and any(a['type']==kind for a in expected['actions'])) and not shareholders.completion_evidence(message,capture_stage(result,preferred_id)):
                raise InvalidProposal('Confirma expresamente el cierre de la lista de participantes')
            company['shareholders_complete' if kind=='finish_shareholders' else 'guarantors_complete']=True
            audit.append({'type':kind,'target_id':company['id'],'evidence':evidence})
            continue
        if kind == 'focus_participant':
            if action['target_id'] not in people or any(action[k] is not None for k in ('source_id','role','field','value')):
                raise InvalidProposal('El participante no pertenece a esta solicitud')
            preferred_id = action['target_id']
            audit.append({'type': kind, 'target_id': preferred_id, 'evidence': evidence})
            continue
        if kind == 'add_participant':
            role = action['role']
            implied=role=='representante' and implicit_representative_input(people,message)
            stage=capture_stage(people,preferred)
            if not active_person(people,preferred) and (role=={'shareholders':'accionista','guarantors':'aval'}.get(stage) or (role=='representante' and pending_representative(people,preferred))):
                reference=identity_reference(message)
                declaration=shareholders.identity_declaration(message) if stage=='shareholders' else None
                implied=implied or bool(declaration or (reference and reference['target_role'] is None) or implicit_list_input(people,message,preferred))
                # The displayed question already asks for this role. The model may
                # interpret a natural answer; literal field evidence is still validated below.
                if not any(mark in message for mark in ('?','¿')) and not re.fullmatch(r'(?:ok|s[ií]|listo|hola|gracias)',message.strip(' .!'),re.I) and not shareholders.completion_evidence(message):
                    implied=True
            if role not in ('aval', 'representante','accionista') or (role not in message.casefold() and not implied) or new_id:
                raise InvalidProposal('Revisa qué participante deseas agregar')
            if action['target_id'] != 'new' or any(action[k] is not None for k in ('source_id', 'field', 'value')):
                raise InvalidProposal('Acción no reconocida')
            if role == 'aval' and sum(p['role'] == 'aval' for p in result.values()) >= 3:
                raise InvalidProposal('Máximo tres avales')
            new_id = str(uuid4())
            result[new_id] = {'id': new_id, 'role': role, 'subject_type': 'PF', 'rfc': None, 'answers': {}}
            if role=='aval' and shareholders.applicant(result):shareholders.applicant(result)['guarantors_complete']=False
            if role=='accionista':
                company=shareholders.applicant(result)
                if not company or company['subject_type']!='PM':raise InvalidProposal('Solo se agregan accionistas a una empresa solicitante')
                company['shareholders_enabled']=True;company['shareholders_complete']=False
                result[new_id]['company_id']=company['id']
            if people and next(iter(people.values())).get('catalog') is not None:result[new_id]['catalog']=next(iter(people.values()))['catalog']
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
            alias=reuse_source(target,field)
            self_name=bool(alias and source_id==target_id)
            if (field not in COMMON_FIELDS and not self_name) or action['value'] is not None:
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
                if field in ('nombre','curp') and source['subject_type'] != 'PF':
                    raise InvalidProposal('La razón social no es el nombre de una persona')
                value = source.get('rfc') if field == 'rfc' else source['answers'].get(alias if self_name else field)
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
            if target['role'] not in ('aval', 'representante','accionista'):
                raise InvalidProposal('El RFC de este participante no se puede cambiar aquí')
            try:
                value, subject = normalized_rfc(value)
            except ValueError:
                raise InvalidProposal('Revisa el formato del RFC')
            if target['role'] == 'representante' and subject != 'PF':
                raise InvalidProposal('El RFC del representante debe corresponder a persona física')
            if any(f not in fields_for(target,subject) for f in target['answers']):
                raise InvalidProposal('El RFC no corresponde al tipo de datos de este participante')
            existing = target.get('rfc')
            target['subject_type'] = subject
        else:
            if field not in fields_for(target):
                raise InvalidProposal('El campo no corresponde a ese participante')
            try:validate_value(target,field,value)
            except ValueError as exc:raise InvalidProposal(str(exc))
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
    try:shareholders.validate_people(result)
    except ValueError as exc:raise InvalidProposal(str(exc))
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
