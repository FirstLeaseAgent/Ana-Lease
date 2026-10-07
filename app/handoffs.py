"""Narrow, state-backed interpretations at participant and field transitions."""
import re
import unicodedata
from .rules import normalized_rfc


def normalized_name(value):
    text=' '.join(value.strip().split()).casefold()
    return ''.join(c for c in unicodedata.normalize('NFD',text) if not unicodedata.combining(c))


def matching_names(people,message):
    wanted=normalized_name(message)
    return [pid for pid,p in people.items() if p['subject_type']=='PF' and
            p['answers'].get('nombre') and normalized_name(p['answers']['nombre'])==wanted]


def literal_name(message):
    text=message.strip()
    if not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ '\-]{2,159}",text):return False
    if not 2<=len(text.split())<=10:return False
    return not re.search(r'\b(ok|hola|gracias|listo|lista|no|sé|se|es|son|soy|tiene|posee|llama|participaci[oó]n|terminamos|acabamos|igual|mismo|misma|contacto|representantes?|aval(?:es)?|accionistas?|socios?|solicitante|agrega|quiero|puedo|ayuda|mañana|seguimos|terminar|continuar|empresa|sociedad|sa|cv)\b',text,re.I)


def pf_rfc(message):
    try:rfc,kind=normalized_rfc(message)
    except ValueError:return False
    return kind=='PF'


def role_answer(message):
    """A short literal answer to cargo/occupation needs no model interpretation."""
    value=message.strip()
    if normalized_name(value) in {'son todos','ya son todos','es todo','eso es todo','ninguno','ninguna'}:return False
    return bool(re.fullmatch(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ &/.-]{0,79}',value)) and not re.search(
        r'\b(no|se|sé|soy|yo|mismo|misma|igual|contacto|representante|aval|accionista|correo|teléfono|rfc|corrige|cambia|agrega|ok|hola|listo|gracias|cuál|cual|qué|que|puedo|puede|ayuda)\b',value,re.I)


def channel_reference(people,relations,history,active,field,message):
    """A shared contact channel may resolve 'igual', but never personal identity."""
    if field not in ('correo_contacto','telefono'):return None
    text=' '.join(message.strip(' .!').casefold().split())
    roles=r'(solicitante|contacto|representante(?: legal)?|aval|accionista)'
    explicit=re.fullmatch(r'(?:es )?(?:el |la )?(?:mismo|misma|igual)?\s*(?:del|de la|que el|que la) '+roles+r'(?: ([1-3]))?',text)
    if explicit:
        role=explicit.group(1).replace(' legal','');index=explicit.group(2)
        ids=[pid for pid,p in people.items() if p['role']==role and pid!=active]
        source=ids[int(index)-1] if index and int(index)<=len(ids) else (ids[0] if not index and len(ids)==1 else None)
        if not source:return {'reply':'Indica cuál '+role+' deseas usar; si hay varios, indica su número.','actions':[]}
    else:
        if not re.fullmatch(r'(?:es |son )?(?:el |la |los )?(?:mismo|misma|mismos|igual)',text):return None
        # Only the immediately preceding saved channel for this target can carry its source.
        previous=None
        for turn in reversed(history):
            if turn.get('capture_targets'):
                previous=turn;break
            intervening=' '.join(turn.get('user','').strip(' .!').casefold().split())
            if intervening and not re.fullmatch(r'(?:es |son )?(?:el |la |los )?(?:mismo|misma|mismos|igual)',intervening):
                return None
        if not previous:return None
        targets=previous['capture_targets']
        prior_fields=[f for t in targets if t.get('id')==active for f in t.get('fields',[])]
        other='correo_contacto' if field=='telefono' else 'telefono'
        if prior_fields!=[other]:return None
        sources={r['source_id'] for r in relations or [] if r.get('type')=='shared_field' and
                 r.get('target_id')==active and r.get('field')==other and
                 r.get('source_id') in people and r['source_id']!=active}
        if len(sources)!=1:return None
        source=next(iter(sources))
    if not people[source]['answers'].get(field):
        return {'reply':'Ese dato aún no está registrado en el participante indicado. Escribe el dato que falta.','actions':[]}
    return {'reply':'Usaré el dato del participante indicado.','actions':[
        {'type':'reuse_field','target_id':active,'source_id':source,'role':None,
         'field':field,'value':None,'evidence':message}]}
