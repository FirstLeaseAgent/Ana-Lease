"""An explicit missing datum is a pending item, never a captured value."""
import re
import unicodedata

def normalize(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFD', value.casefold()) if not unicodedata.combining(c)).strip(' .!,;').split())

def unavailable(message, field=None):
    text = normalize(message)
    text = re.sub(r'^(?:aun|todavia|por ahora) ', '', text)
    if '?' in text or '¿' in text:
        return False
    aliases = {'curp':['curp'], 'rfc':['rfc'], 'telefono':['telefono','numero','celular'],
               'correo_contacto':['correo','correo electronico','email'],
               'pagina_web':['pagina web','pagina','sitio web','web'],
               'porcentaje_participacion':['porcentaje','porcentaje de participacion']}
    nouns = ['dato','informacion'] + (aliases.get(field, [field.replace('_',' ')]) if field else [])
    noun = '(?:' + '|'.join(re.escape(normalize(n)) for n in nouns) + ')'
    obj = r'(?:(?:ese|esa|este|esta|el|la|mi|su|un|una)\s+)?' + noun
    return bool(re.fullmatch(
        r'(?:no (?:lo |la )?tengo(?: '+obj+r')?|no cuento con '+obj+r'|'
        r'no (?:lo |la |me lo |me la )?se(?: cual es)?|no recuerdo(?: '+obj+r')?|(?:lo )?desconozco(?: '+obj+r')?|'
        r'no conozco '+obj+r'|no (?:esta|lo tengo|tengo '+obj+r') disponible|'
        r'no proporcionado|(?:lo |la )?(?:proporcionare|entregare|compartire) (?:despues|mas tarde)|'
        r'prefiero (?:proporcionarlo|entregarlo|compartirlo) despues)'
        r'(?: (?:ahora|ahorita|por ahora|por el momento|en este momento|aun|todavia|a la mano))?'
        r'(?:[,;]? (?:continuemos|sigamos|siguiente pregunta|podemos continuar))?', text))

def subject_type(message):
    text = normalize(message)
    if re.fullmatch(r'(?:es |soy |una )?(?:pf|persona fisica|fisica)', text):return 'PF'
    if re.fullmatch(r'(?:es |una )?(?:pm|persona moral|moral|empresa)', text):return 'PM'
    return None
