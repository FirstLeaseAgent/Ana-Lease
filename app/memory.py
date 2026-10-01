"""Rebuild intake memory from committed turns, never from model summaries."""
from .catalog import fields_for, reuse_source

MEMORY_QUERY = """
SELECT DISTINCT ON (a.action->>'target_id', COALESCE(a.action->>'field','__identity__:' || COALESCE(a.action->>'source_id','')))
       a.action
FROM capture_turns t
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(t.audit_json,'[]'::jsonb))
     WITH ORDINALITY AS a(action, action_order)
WHERE t.intake_id=%s AND t.status='complete'
  AND a.action->>'type' IN ('reuse_field','save_field','undo_save_field','confirm_identity')
ORDER BY a.action->>'target_id', COALESCE(a.action->>'field','__identity__:' || COALESCE(a.action->>'source_id','')),
         t.updated_at DESC, t.request_id DESC, a.action_order DESC
"""


def value_for(person, field):
    return person.get('rfc') if field == 'rfc' else person['answers'].get(field)


def identity_members(people, relations, participant_id):
    linked={participant_id}
    for _ in range(len(people)):
        for relation in relations:
            if relation.get('type')!='same_person':continue
            pair={relation['target_id'],relation['source_id']}
            if pair & linked:linked.update(pair & people.keys())
    return linked


def relations_from_actions(people, actions, session_email=None):
    """Accept only current, matching data from participants in this intake."""
    relations=[]
    for action in actions:
        target_id=action.get('target_id');source_id=action.get('source_id')
        if target_id not in people:
            continue
        target=people[target_id]
        if action.get('type')=='confirm_identity':
            if source_id not in people or source_id==target_id:
                continue
            source=people[source_id]
            shared=('nombre','correo_contacto','telefono')
            if target['subject_type']!='PF' or source['subject_type']!='PF':
                continue
            matching_name=bool(value_for(source,'nombre') and value_for(target,'nombre')==value_for(source,'nombre'))
            matching_rfc=bool(source.get('rfc') and target.get('rfc')==source['rfc'])
            if not (matching_name or matching_rfc):
                continue
            if source.get('rfc') and target.get('rfc') and source['rfc']!=target['rfc']:
                continue
            if any(value_for(target,f) and value_for(source,f) and
                   value_for(target,f)!=value_for(source,f) for f in shared):
                continue
            relations.append({'type':'same_person','target_id':target_id,
                              'source_id':source_id,'source_role':source['role']})
            continue
        if action.get('type')!='reuse_field':
            continue
        field=action.get('field')
        if field not in fields_for(target) and field!='rfc':
            continue
        if source_id=='session_user':
            source_value=session_email if field=='correo_contacto' else None
            source_role='usuario_verificado'
            source_field=field
        elif source_id in people:
            source=people[source_id]
            source_field=(reuse_source(target,field) if source_id==target_id else field) or field
            source_value=value_for(source,source_field)
            source_role=source['role']
        else:
            continue
        if source_value and value_for(target,field)==source_value:
            relations.append({'type':'shared_field','target_id':target_id,'source_id':source_id,
                              'source_role':source_role,'field':field,'source_field':source_field})
    # A contact without RFC must not bridge two different known RFCs.
    invalid=set()
    for pid in people:
        members=identity_members(people,relations,pid)
        rfcs={people[mid]['rfc'] for mid in members if people[mid].get('rfc')}
        if len(rfcs)>1:invalid.update(members)
    return [r for r in relations if r['type']!='same_person' or
            not ({r['target_id'],r['source_id']} & invalid)]


def load_relations(conn, intake_id, people, session_email=None):
    rows=conn.execute(MEMORY_QUERY,(intake_id,)).fetchall()
    return relations_from_actions(people,[row['action'] for row in rows],session_email)


def model_memory(relations, history):
    # The last three turns are already delivered in history. Earlier turns remain
    # available here; durable facts are reconstructed across the entire intake.
    return {
        'version':1,
        'guidance':('Memoria de esta solicitud. Las relaciones proceden de cambios guardados y '
                    'verificados por el servidor. same_person confirma identidad; shared_field '
                    'solo confirma reutilización de ese campo, no identidad. Usa estas relaciones '
                    'para resolver referencias; known_fields, missing_fields y current_question '
                    'actuales prevalecen sobre el historial. Una identidad PF confirmada permite '
                    'reutilizar nombre, RFC, correo y teléfono disponibles en sus roles vinculados. '
                    'Cargo, ocupación y autorizaciones son propios de cada rol. '
                    'No declares guardado un dato sin '
                    'proponer su acción. No sobrescribas valores ni uses otros expedientes.'),
        'confirmed_relations':relations,
        'earlier_turns':[{'user':t['user'][:2000],'assistant':t['assistant'][:1800],
                         'capture_targets':t.get('capture_targets',[])} for t in history[:-3]],
    }
