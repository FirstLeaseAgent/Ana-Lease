import os
import json
import unittest
from unittest.mock import patch
from uuid import uuid4
from fastapi import FastAPI, HTTPException
from app import quotes
from test_documents import FakeConn, FakePool

class QuotesTests(unittest.TestCase):
    def body(self, down='20%', value='500000'):
        return quotes.QuoteInput(request_id=uuid4(),name='Cliente Prueba',phone='5512345678',asset='Activo prueba',value=value,down_payment=down)

    def test_percent_fraction_and_amount_produce_same_api_input(self):
        for down in ['20%', '20', '0.2', '0,2', '100 mil', '$100,000', '100000', '100000 pesos']:
            self.assertEqual(quotes.normalize(self.body(down))['enganche'],20)

    def test_bounds_inclusive_and_invalid_not_clamped(self):
        for down in ['10%','0.1','40%','0.4']:
            self.assertTrue(10<=quotes.normalize(self.body(down))['enganche']<=40)
        for down in ['9%','41%','0','1','texto','-10%','40 mil%','9.9999%','40.0001%']:
            with self.assertRaises(HTTPException): quotes.normalize(self.body(down))

    def test_no_commercial_defaults_or_client_override_forwarded(self):
        body=self.body().model_copy(update={'tasa_anual':1})
        self.assertEqual(set(quotes.normalize(body)),{'nombre','nombre_activo','valor','enganche','phone_number'})

    def test_missing_or_bad_value_rejected(self):
        for value in ['0','-100','NaN','Infinity','500.001','100000001']:
            with self.assertRaises(HTTPException):quotes.normalize(self.body(value=value))

    def test_configured_target_and_token_required(self):
        for env in [{'N8N_QUOTE_WEBHOOK_URL':'https://evil.example','N8N_QUOTE_WEBHOOK_TOKEN':'test-secret'}, {'N8N_QUOTE_WEBHOOK_URL':'https://flagent.app.n8n.cloud/webhook/Analease-Cotizacion','N8N_QUOTE_WEBHOOK_TOKEN':''}]:
            with patch.dict(os.environ,env),patch.object(quotes,'build_opener') as opener:
                with self.assertRaises(HTTPException):quotes.generate({})
                opener.assert_not_called()

    def test_pdf_validated_and_no_retry_on_error(self):
        class Res:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return b'%PDF-1.7\nexample'
        with patch.dict(os.environ,{'N8N_QUOTE_WEBHOOK_URL':'https://flagent.app.n8n.cloud/webhook/Analease-Cotizacion','N8N_QUOTE_WEBHOOK_TOKEN':'test-secret'}),patch.object(quotes,'build_opener') as opener:
            opener.return_value.open.return_value=Res()
            self.assertTrue(quotes.generate({}).startswith(b'%PDF-'))
            opener.return_value.open.side_effect=TimeoutError()
            with self.assertRaises(HTTPException):quotes.generate({})
            self.assertEqual(opener.return_value.open.call_count,2)

    def test_redirects_refused(self):
        self.assertIsNone(quotes.NoRedirect().redirect_request(None,None,None,None,None,None))

    def test_pdf_response_has_safe_filename(self):
        res=quotes.pdf_response(b'%PDF-')
        self.assertEqual(res.media_type,'application/pdf')
        self.assertIn('FirstLease-Cotizacion.pdf',res.headers['content-disposition'])

    def route(self, previous=None):
        class Conn(FakeConn):
            def fetchone(self):
                if 'FROM quote_dispatches WHERE id=' in self.query:return previous
                if 'count(*)' in self.query:return {'total':0,'ip':0}
                return None
        conn=Conn(); app=FastAPI()
        quotes.install(app,FakePool(conn),lambda value:value,lambda request:'test-ip')
        return next(r.endpoint for r in app.routes if r.path=='/quotes'), conn

    def test_cached_retry_returns_pdf_without_duplicate_webhook(self):
        body=self.body()
        previous={'payload_hash':'quote:'+json.dumps(quotes.normalize(body),sort_keys=True),'status':'ready','fresh':True,'pdf':b'%PDF-cache'}
        route,conn=self.route(previous)
        with patch.dict(os.environ,{'N8N_QUOTE_WEBHOOK_TOKEN':'test'}),patch.object(quotes,'generate') as generate:
            self.assertEqual(route(body,None).body,b'%PDF-cache')
            generate.assert_not_called()

    def test_unknown_or_processing_retry_does_not_resend(self):
        body=self.body()
        for status in ['processing','unconfirmed']:
            previous={'payload_hash':'quote:'+json.dumps(quotes.normalize(body),sort_keys=True),'status':status,'fresh':True,'pdf':None}
            route,conn=self.route(previous)
            with patch.dict(os.environ,{'N8N_QUOTE_WEBHOOK_TOKEN':'test'}),patch.object(quotes,'generate') as generate:
                with self.assertRaises(HTTPException) as err:route(body,None)
                self.assertEqual(err.exception.status_code,409)
                generate.assert_not_called()

    def test_claim_committed_before_webhook_and_failure_marked(self):
        route,conn=self.route();body=self.body()
        def fail(payload):
            self.assertTrue(any(q.startswith('INSERT INTO quote_dispatches') for q,_ in conn.calls))
            raise HTTPException(503,'unconfirmed')
        with patch.dict(os.environ,{'N8N_QUOTE_WEBHOOK_TOKEN':'test'}),patch.object(quotes,'generate',side_effect=fail) as generate:
            with self.assertRaises(HTTPException):route(body,None)
            generate.assert_called_once()
            self.assertTrue(any("status='unconfirmed'" in q for q,_ in conn.calls))
