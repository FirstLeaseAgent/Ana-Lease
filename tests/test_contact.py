import json
import os
import unittest
from unittest.mock import patch
from uuid import UUID
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from app import contact
from test_documents import FakeConn, FakePool, INTAKE

CONTACT = UUID('00000000-0000-4000-8000-000000000009')

class Conn(FakeConn):
    previous = None
    count = 0
    email = 'equipo@example.test'
    owned = True
    claim = True
    def fetchone(self):
        if 'FROM contact_requests WHERE id=' in self.query:return self.previous
        if 'count(*)' in self.query:return {'n': self.count}
        if 'FROM intakes' in self.query:return {'id': INTAKE} if self.owned else None
        if 'SELECT email FROM users' in self.query:return {'email': self.email}
        if 'RETURNING id,name,phone' in self.query:return {'id': CONTACT,'name': 'Persona de prueba','phone': '5512345678'} if self.claim else None
        if 'SELECT id FROM contact_requests' in self.query:return {'id': CONTACT}
        return None

class Req:
    cookies = {}

class FakeResponse:
    status = 200
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return b'{"ok":true}'

class ContactTests(unittest.TestCase):
    def body(self,**kwargs):return contact.ContactInput(request_id=CONTACT,name='Persona de prueba',phone='55 1234 5678',**kwargs)
    def routes(self,conn=None,owner=None):
        conn=conn or Conn();app=FastAPI()
        def no_session(request):raise HTTPException(401,'Verifica correo')
        contact.install(app,FakePool(conn),owner or no_session,lambda s:'hash:'+s,lambda r:'test-ip')
        return {r.path:r.endpoint for r in app.routes},conn
    def test_validates_and_normalizes_name_and_international_phone(self):
        out=contact.ContactInput(request_id=CONTACT,name='  Persona   de prueba  ',phone='+52 (55) 1234-5678')
        self.assertEqual(out.name,'Persona de prueba');self.assertEqual(out.phone,'+525512345678')
        for phone in ['55123','5512345678901234','0000000000','5512345678 ext 9','+52\n5512345678']:
            with self.assertRaises(ValidationError):contact.ContactInput(request_id=CONTACT,name='Nombre',phone=phone)
        with self.assertRaises(ValidationError):contact.ContactInput(request_id=CONTACT,name='12',phone='5512345678')
    def test_anonymous_callback_is_persisted_before_delivery(self):
        routes,conn=self.routes()
        def delivery(pool,request_id):
            self.assertEqual(request_id,CONTACT)
            self.assertTrue(any(q.startswith('INSERT INTO contact_requests') for q,_ in conn.calls))
        with patch.object(contact,'deliver',delivery):out=routes['/contact-requests'](self.body(),Req())
        self.assertTrue(out['ok'])
        args=next(a for q,a in conn.calls if q.startswith('INSERT INTO contact_requests'))
        self.assertEqual(args[-2:],(None,None));self.assertEqual(args[2],'5512345678')
    def test_idempotent_request_does_not_resend_or_insert(self):
        conn=Conn();conn.previous={'name':'Persona de prueba','phone':'5512345678','intake_id':None,'owner_id':None};routes,_=self.routes(conn)
        with patch.object(contact,'deliver') as deliver:self.assertTrue(routes['/contact-requests'](self.body(),Req())['ok']);deliver.assert_not_called()
        self.assertFalse(any(q.startswith('INSERT') for q,_ in conn.calls))
        conn.previous['phone']='5598765432'
        with self.assertRaises(HTTPException) as e:routes['/contact-requests'](self.body(),Req())
        self.assertEqual(e.exception.status_code,409)
    def test_linking_an_intake_requires_its_owner(self):
        routes,conn=self.routes()
        with self.assertRaises(HTTPException) as e:routes['/contact-requests'](self.body(intake_id=INTAKE),Req())
        self.assertEqual(e.exception.status_code,401)
        conn.owned=False;routes,_=self.routes(conn,lambda r:'user')
        with self.assertRaises(HTTPException) as e:routes['/contact-requests'](self.body(intake_id=INTAKE),Req())
        self.assertEqual(e.exception.status_code,404)
        self.assertFalse(any(q.startswith('INSERT') for q,_ in conn.calls))
    def test_rate_limit_prevents_email_and_insertion(self):
        conn=Conn();conn.count=3;routes,_=self.routes(conn)
        with patch.object(contact,'deliver') as deliver:
            with self.assertRaises(HTTPException) as e:routes['/contact-requests'](self.body(),Req())
            self.assertEqual(e.exception.status_code,429);deliver.assert_not_called()
        self.assertFalse(any(q.startswith('INSERT') for q,_ in conn.calls))
    def test_email_failure_keeps_record_and_can_retry(self):
        conn=Conn()
        with patch.object(contact,'send_contact',side_effect=RuntimeError('offline')):contact.deliver(FakePool(conn),CONTACT)
        self.assertEqual(conn.calls[-1][1],('failed',CONTACT))
        with patch.object(contact,'send_contact') as send:contact.deliver(FakePool(conn),CONTACT);send.assert_called_once()
        self.assertEqual(conn.calls[-1][1],('sent',CONTACT))
        conn.claim=False
        with patch.object(contact,'send_contact') as send:contact.deliver(FakePool(conn),CONTACT);send.assert_not_called()
    def test_admin_only_listing_and_retry(self):
        routes,conn=self.routes(owner=lambda r:'user')
        with patch.dict(os.environ,{'CAPTURE_CONFIG_ADMIN_EMAILS':'other@example.test'}):
            for path,args in [('/admin/contact-requests',(Req(),)),('/admin/contact-requests/{contact_id}/notification/retry',(CONTACT,Req()))]:
                with self.assertRaises(HTTPException) as e:routes[path](*args)
                self.assertEqual(e.exception.status_code,403)
        self.assertFalse(any('name,c.phone' in q for q,_ in conn.calls))
        with patch.dict(os.environ,{'CAPTURE_CONFIG_ADMIN_EMAILS':'equipo@example.test'}):self.assertEqual(routes['/admin/contact-requests'](Req(),contact_id=CONTACT)['contacts'],[])
        self.assertTrue(any(a==(CONTACT,CONTACT,0) for _,a in conn.calls))
    def test_mail_contains_requested_contact_only_and_fixed_team_destination(self):
        env={'N8N_MAIL_WEBHOOK_URL':'https://flagent.app.n8n.cloud/webhook/analease-otp-send','N8N_MAIL_WEBHOOK_TOKEN':'test-token','PUBLIC_ORIGIN':'https://portal.example.test'}
        with patch.dict(os.environ,env),patch.object(contact,'urlopen',return_value=FakeResponse()) as send:
            contact.send_contact({'id':CONTACT,'name':'Persona de prueba','phone':'5512345678'})
        req=send.call_args.args[0];payload=json.loads(req.data)
        self.assertEqual(payload['event'],'contact_requested');self.assertEqual(payload['email'],'aortega@firstlease.com.mx')
        self.assertEqual(payload['name'],'Persona de prueba');self.assertEqual(payload['phone'],'5512345678')
        self.assertEqual(payload['followup_url'],'https://portal.example.test/seguimiento?contacto='+str(CONTACT))
        self.assertNotIn('rfc',payload);self.assertEqual(req.get_header('X-analease-token'),'test-token')
