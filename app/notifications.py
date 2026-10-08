"""Recorded submission notices; delivery failure never discards an intake."""
import json
import logging
import os
from urllib.request import Request,urlopen
from urllib.parse import urlsplit

logger=logging.getLogger(__name__)


def send_notice(intake_id,missing):
    url=os.environ['N8N_MAIL_WEBHOOK_URL']
    parts=urlsplit(url)
    if parts.scheme!='https' or parts.netloc!='flagent.app.n8n.cloud' or parts.username or parts.password:
        raise RuntimeError('Invalid notification webhook')
    origin=os.environ.get('PUBLIC_ORIGIN') or os.environ['RENDER_EXTERNAL_URL']
    payload={'event':'submitted','email':'aortega@firstlease.com.mx','intake_id':str(intake_id),
             'required_missing':missing,'followup_url':origin+'/seguimiento?solicitud='+str(intake_id)}
    req=Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-AnaLease-Token':os.environ['N8N_MAIL_WEBHOOK_TOKEN']},method='POST')
    with urlopen(req,timeout=15) as response:
        if response.status!=200 or json.load(response)!={'ok':True}:raise RuntimeError('Notice unconfirmed')


def deliver(pool,intake_id):
    with pool.connection() as conn:
        row=conn.execute("UPDATE submission_notifications SET status='sending',attempts=attempts+1,updated_at=now() WHERE intake_id=%s AND (status IN ('pending','failed') OR (status='sending' AND updated_at<now()-interval '2 minutes')) RETURNING required_missing",(intake_id,)).fetchone()
    if not row:return
    try:
        send_notice(intake_id,row['required_missing'])
        status='sent'
    except Exception:
        logger.warning('Submission notice unconfirmed; available for retry')
        status='failed'
    with pool.connection() as conn:
        conn.execute('UPDATE submission_notifications SET status=%s,updated_at=now() WHERE intake_id=%s',(status,intake_id))
