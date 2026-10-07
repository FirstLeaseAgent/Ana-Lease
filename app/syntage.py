"""Private n8n adapter. Never return the raw status to the public browser."""
import http.client
import json
import os
from urllib.parse import urlsplit


class SyntageUnavailable(Exception):
    pass


def check_status(rfc: str) -> dict:
    url = os.environ['N8N_SYNTAGE_WEBHOOK_URL']
    token = os.environ['N8N_SYNTAGE_WEBHOOK_TOKEN']
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc != 'flagent.app.n8n.cloud'
            or parts.path != '/webhook/analease-syntage-status'
            or parts.query or parts.fragment or not token):
        raise SyntageUnavailable('Configuración inválida')

    payload = json.dumps({'rfc': rfc}).encode('utf-8')
    conn = http.client.HTTPSConnection(parts.hostname, timeout=20)
    try:
        conn.request('POST', parts.path, body=payload, headers={
            'Content-Type': 'application/json',
            'X-AnaLease-Token': token,
        })
        response = conn.getresponse()
        if response.status != 200:
            raise SyntageUnavailable(f'n8n_http_{response.status}')
        data = json.loads(response.read(4097))
    except (OSError, ValueError) as exc:
        raise SyntageUnavailable('n8n no está disponible') from exc
    finally:
        conn.close()

    if not isinstance(data, dict) or data.get('ok') is not True:
        raise SyntageUnavailable('Respuesta inconclusa')
    if (data.get('person_type') not in ('legal', 'physical')
            or data.get('registered') not in (True, False)
            or data.get('sat') not in ('valid', 'pending', 'invalid', 'missing')
            or data.get('buro') not in ('valid', 'missing')
            or data.get('next_action') not in ('continue', 'wait', 'onboarding')
            or data['person_type'] != ('legal' if len(rfc) == 12 else 'physical')):
        raise SyntageUnavailable('Respuesta no reconocida')
    return {key: data[key] for key in ('person_type', 'registered', 'sat', 'buro', 'next_action')}
