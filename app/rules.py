import re

RFC_RE = re.compile(r'^(?:[A-ZÑ&]{3}[0-9]{6}[A-Z0-9]{3}|[A-ZÑ&]{4}[0-9]{6}[A-Z0-9]{3})$')
FIELDS = {
    'accionista': {'PF': {'nombre', 'curp', 'porcentaje_participacion'}, 'PM': {'razon_social', 'porcentaje_participacion'}},
    'contacto': {'PF': {'nombre', 'correo_contacto', 'telefono'}, 'PM': set()},
    'solicitante': {
        'PF': {'nombre', 'correo_contacto', 'telefono', 'actividad', 'pagina_web'},
        'PM': {'razon_social', 'nombre_comercial', 'pagina_web', 'correo_contacto', 'telefono', 'actividad'},
    },
    'aval': {
        'PF': {'nombre', 'correo_contacto', 'telefono', 'ocupacion'},
        'PM': {'razon_social', 'pagina_web', 'correo_contacto', 'telefono'},
    },
    'representante': {
        'PF': {'nombre', 'cargo', 'correo_contacto', 'telefono'},
        'PM': set(),
    },
}

def normalized_rfc(value: str) -> tuple[str, str]:
    value = value.strip().upper()
    if not RFC_RE.fullmatch(value):
        raise ValueError('RFC inválido')
    return value, 'PF' if len(value) == 13 else 'PM'

def participant_from_rfc(role: str, value: str) -> tuple[str, str]:
    rfc, subject = normalized_rfc(value)
    if role == 'representante' and subject != 'PF':
        raise ValueError('El RFC del representante debe corresponder a una persona física')
    return rfc, subject

def allowed_field(role: str, subject_type: str, field: str) -> bool:
    return field in FIELDS.get(role, {}).get(subject_type, set())

def masked_name(value: str) -> str:
    """Give a recognizable hint without returning the captured name."""
    return re.sub(r'[\wÑñ]+', lambda match: match.group(0)[:1] + '*' * (len(match.group(0)) - 1), value[:120])
