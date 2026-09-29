"""Deliver an OTP to the dedicated n8n mail webhook, never to the browser."""
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def send_code(email: str, code: str):
    url = os.environ['N8N_MAIL_WEBHOOK_URL']
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password or parts.fragment:
        raise RuntimeError('Webhook n8n debe tener una URL HTTPS válida')
    req = Request(
        url,
        data=json.dumps({'email': email, 'code': code}).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'X-AnaLease-Token': os.environ['N8N_MAIL_WEBHOOK_TOKEN'],
        },
        method='POST',
    )
    with urlopen(req, timeout=15) as response:
        if response.status != 200 or json.load(response) != {'ok': True}:
            raise RuntimeError('n8n no confirmó el envío del código')
