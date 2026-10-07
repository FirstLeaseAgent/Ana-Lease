"""Company-scoped shareholder capture, with explicit list completion."""
import re
from decimal import Decimal, InvalidOperation

MAX_SHAREHOLDERS = 3  # Three records in the supplied company application.
CURP = re.compile(r'^[A-Z][AEIOUX][A-Z]{2}[0-9]{6}[HM][A-Z]{5}[A-Z0-9][0-9]$')


def identity_declaration(message):
    """Read a single explicit identity plus percentage, without inferring from channels."""
    text=' '.join(message.casefold().strip(' .!').split())
    number=r'\d{1,3}(?:[.,]\d{1,4})?\s*%'
    source=r'(?:contacto|representante(?: legal)?|aval|accionista)(?: [1-3])?'
    identity=r'es (?:el mismo|la misma)(?: que)? (?:el |la )?'+source
    patterns=[
        r'(?:(?:el |la )?(?:principal|accionista) )?(?:con |tiene |posee )(?P<percent>'+number+r')(?: de participaci[oó]n)? (?P<identity>'+identity+r')',
        r'(?:(?:el |la )?accionista )?(?P<identity>'+identity+r')[,;]? (?:y )?(?:tiene|posee|con) (?:el )?(?P<percent>'+number+r')(?: de participaci[oó]n)?',
    ]
    match=next((m for pattern in patterns if (m:=re.fullmatch(pattern,text))),None)
    if not match:return None
    # Keep the user's exact numeric text for evidence validation.
    literal=re.search(number,message)
    return {'identity':match['identity'],'percentage':literal.group(0)}


def list_finished(message):
    text=' '.join(message.casefold().strip(' .!').split())
    return text in {'no hay más','no hay mas','ya no hay más','ya no hay mas','no hay otros',
                    'son todos','ya son todos','es todo','eso es todo','no faltan más','no faltan mas',
                    'no hay ninguno','ninguno','ninguna'}


def completion_evidence(message,stage=None):
    """Require an express ending; a model proposal or an acknowledgement isn't consent."""
    text=' '.join(message.casefold().strip(' .!').split())
    if '?' in text or '¿' in text:return False
    if stage=='guarantors' and re.search(r'\b(accionistas|socios)\b',text):return False
    if stage=='shareholders' and re.search(r'\bavales\b',text):return False
    if list_finished(message):return True
    return bool(re.fullmatch(
        r'(?:ya )?(?:termin[eé]|terminamos|he terminado|acab[eé]|acabamos)(?: de (?:registrar|agregar|capturar)(?: (?:a )?(?:los |mis |todos los )?(?:accionistas|socios|avales))?)?|'
        r'(?:con (?:[eé]l|ella|ellos|ellas|este|esta) )?(?:terminamos|acabamos)|'
        r'(?:ya )?(?:est[aá]n|quedaron) todos(?: (?:los |mis )?(?:accionistas|socios|avales))?|'
        r'no (?:tengo|hay|agregar[eé]) (?:m[aá]s|otros)(?: (?:accionistas|socios|avales))?',text))


def applicant(people):
    return next((p for p in people.values() if p['role']=='solicitante'), None)


def percentage(value):
    text=str(value).strip()
    if not re.fullmatch(r'\d{1,3}(?:[.,]\d{1,4})?\s*%?', text):
        raise ValueError('Escribe un porcentaje numérico, por ejemplo 25%')
    try: number=Decimal(text.rstrip('%').strip().replace(',','.'))
    except InvalidOperation: raise ValueError('Revisa el porcentaje de participación')
    if not Decimal('10') < number <= Decimal('100'):
        raise ValueError('Capturamos principales accionistas con participación mayor al 10% y hasta 100%')
    return number


def validate_people(people):
    company=applicant(people)
    rows=[p for p in people.values() if p['role']=='accionista']
    if not rows:return
    if not company or company['subject_type']!='PM':
        raise ValueError('Solo se agregan accionistas a un solicitante persona moral')
    if len(rows)>MAX_SHAREHOLDERS:raise ValueError('El formato permite hasta tres principales accionistas')
    rfcs=[p['rfc'] for p in rows if p.get('rfc')]
    if len(set(rfcs))!=len(rfcs):raise ValueError('Ese accionista ya está registrado en esta empresa')
    total=Decimal('0')
    for p in rows:
        if p.get('company_id')!=company['id']:
            raise ValueError('El accionista no está vinculado con la empresa solicitante')
        value=p['answers'].get('porcentaje_participacion')
        if value:total+=percentage(value)
    if total>100:raise ValueError('La participación de los accionistas no puede sumar más de 100%')


def needs_completion(people):
    company=applicant(people)
    return bool(company and company['subject_type']=='PM' and
                company.get('shareholders_enabled') and not company.get('shareholders_complete'))


def action(kind,target='new',role=None,field=None,value=None,message=''):
    return {'type':kind,'target_id':target,'source_id':None,'role':role,
            'field':field,'value':value,'evidence':message}


def local_proposal(people,message,active,missing,stage):
    """New-role lifecycle works with the current n8n schema, without an API change."""
    text=' '.join(message.casefold().strip(' .!').split())
    add=re.fullmatch(r'(?:(?:quiero )?(?:agrega|agregar|añade|añadir) )?(?:(?:un|una|otro|otra|al|el) )?accionista(?:\s+([a-zñ&0-9]{12,13}))?',text)
    if add:
        company=applicant(people)
        if not company or company['subject_type']!='PM':
            return {'reply':'Los accionistas corresponden a una empresa solicitante persona moral.','actions':[]}
        if sum(p['role']=='accionista' for p in people.values())>=MAX_SHAREHOLDERS:
            return {'reply':'Ya se registraron los tres accionistas del formato. Escribe «listo accionistas» cuando hayas terminado.','actions':[]}
        actions=[action('add_participant',role='accionista',message=message)]
        if add.group(1):actions.append(action('save_field',field='rfc',value=message.split()[-1],message=message))
        return {'reply':'Vamos a registrar al accionista.','actions':actions}
    closing=text in {'listo accionistas','ya agregué todos los accionistas','ya agregue todos los accionistas',
                     'no hay más accionistas','no hay mas accionistas',
                     'no hay accionistas con más del 10%','no hay accionistas con mas del 10%',
                     'no hay accionistas con participación mayor al 10%','no hay accionistas con participacion mayor al 10%'}
    if closing or (stage=='shareholders' and not active and list_finished(message)):
        company=applicant(people)
        if stage!='shareholders' or active or not needs_completion(people):
            return {'reply':'Primero completa los datos pendientes de la empresa, representante y accionistas.','actions':[]}
        return {'reply':'La lista de accionistas quedó confirmada.','actions':[
            action('finish_shareholders',target=company['id'],message=message)]}
    if stage=='guarantors' and not active:
        if list_finished(message) or text in {'sin aval','sin avales','listo avales','no agregar aval','no agregar avales'}:
            company=applicant(people)
            return {'reply':'La lista de avales quedó confirmada.','actions':[action('finish_guarantors',target=company['id'],message=message)]}
        if text not in {'aval','agrega un aval','agregar aval','otro aval','agrega otro aval'}:
            return {'reply':'¿Quién será el aval? Puedes indicar que es la misma persona de otro rol o responder «sin aval».','actions':[]} if text in {'ok','sí','si','listo'} else None
    if stage=='shareholders' and not active:
        return {'reply':'¿Quién tiene más del 10% de la empresa? Puedes indicar la persona y su porcentaje en una respuesta, o decir «no hay más».','actions':[]} if text in {'ok','sí','si','listo'} else None
    if not active or people[active]['role']!='accionista':return None
    field=missing(people[active])[0]
    # Short questions, commands and references are never stored as identity data.
    if any(mark in message for mark in ('?','¿')) or re.search(
        r'\b(mismo|misma|igual|agrega|representante|contacto|aval|accionista|corrige|cambia|no sé|no se|hola|ok|listo)\b',text):
        return None
    if re.search(r'\b(tiene|soy|es|llama|participaci[oó]n|porcentaje|ayuda|puedo|termin[eé]|terminamos)\b',text):return None
    return {'reply':'Dato guardado.','actions':[action('save_field',target=active,field=field,value=message.strip(),message=message)]}
