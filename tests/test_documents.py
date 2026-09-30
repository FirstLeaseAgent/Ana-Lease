import asyncio
import copy
import unittest
from contextlib import nullcontext
from datetime import datetime,timezone
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI,HTTPException
from app import documents as d
from app import document_routes
from app.conversation import capture_stage,resume_reply

INTAKE=UUID('00000000-0000-4000-8000-000000000001')
APP=UUID('00000000-0000-4000-8000-000000000002')
REP=UUID('00000000-0000-4000-8000-000000000003')
AVAL=UUID('00000000-0000-4000-8000-000000000004')
UPLOAD=UUID('00000000-0000-4000-8000-000000000005')

def complete_people():
    return {
        str(APP):{'id':str(APP),'role':'solicitante','subject_type':'PM','rfc':'ABC010101AB1','answers':{'razon_social':'Empresa de prueba','nombre_comercial':'Prueba','actividad':'Servicios','pagina_web':'example.test','correo_contacto':'prueba@example.test','telefono':'5551234567'}},
        str(REP):{'id':str(REP),'role':'representante','subject_type':'PF','rfc':'ABCD010101AB1','answers':{'nombre':'Nombre de prueba','cargo':'Socio','correo_contacto':'prueba@example.test','telefono':'5551234567'}},
        str(AVAL):{'id':str(AVAL),'role':'aval','subject_type':'PF','rfc':'ABCD010101AB1','answers':{'nombre':'Nombre de prueba','ocupacion':'Socio','correo_contacto':'prueba@example.test','telefono':'5551234567'}}}

class FakeConn:
    def __init__(self):self.calls=[];self.query='';self.states=[];self.previous=None;self.source_upload=None
    def transaction(self):return nullcontext()
    def execute(self,query,params=()):self.query=query;self.calls.append((query,params));return self
    def fetchall(self):return self.states if 'FROM document_states' in self.query else []
    def fetchone(self):
        if 'FROM document_uploads WHERE id=' in self.query:return self.previous
        if 'JOIN document_uploads' in self.query:return self.source_upload
        return {'n':0} if 'count(*)' in self.query else None

class FakePool:
    def __init__(self,conn):self.conn=conn
    def connection(self):return nullcontext(self.conn)

class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.people=complete_people()
        # Synthetic requirements exercise the engine without publishing an
        # organization's private catalogue or required/optional policy.
        fixture=[]
        examples=[('PM','Solicitante','documento_empresa',1,True,None),
                  ('PM','RepresentanteLegal','documento_persona',1,True,None),
                  ('PM','RepresentanteLegal','documento_opcional',2,False,None),
                  ('PF','Aval','documento_persona',1,True,None),
                  ('PF','Aval','documento_condicional',2,True,'estado_civil')]
        for scope,role,code,order,required,field in examples:
            fixture.append({'scope':scope,'role':role,'code':code,'label':'Documento ficticio','order':order,'required':required,'dependency_field':field,'dependency_value':'Casado' if field else None})
        patcher=patch.object(d,'CATALOG',fixture);patcher.start();self.addCleanup(patcher.stop)
    def routes(self,conn=None,authorize=None,people=None):
        conn=conn or FakeConn()
        app=FastAPI()
        document_routes.install(app,FakePool(conn),lambda request:'owner',authorize or (lambda *args,**kwargs:None),lambda *args:people or self.people,lambda *args:True)
        return {route.path:route.endpoint for route in app.routes},conn

    def test_complete_capture_advances_and_incomplete_or_missing_pm_rep_stays(self):
        self.assertEqual(capture_stage(self.people),'documents')
        self.assertIn('Continuemos con los documentos',resume_reply(self.people))
        missing=copy.deepcopy(self.people);missing[str(REP)]['answers'].pop('cargo')
        self.assertEqual(capture_stage(missing),'capture')
        missing=copy.deepcopy(self.people);missing.pop(str(REP))
        self.assertEqual(capture_stage(missing),'capture')
        self.assertIn('representante legal',resume_reply(missing))
        missing=copy.deepcopy(self.people);missing.pop(str(AVAL))
        self.assertEqual(capture_stage(missing),'documents')

    def test_catalog_uses_own_aval_type_and_company_context_for_pf_representative(self):
        rows=d.requirements(self.people)
        self.assertEqual(len(d.CATALOG),5)
        self.assertEqual([r['code'] for r in rows if r['participant_id']==str(APP)],['documento_empresa'])
        self.assertEqual([r['code'] for r in rows if r['participant_id']==str(REP)],['documento_persona','documento_opcional'])
        self.assertEqual([r['code'] for r in rows if r['participant_id']==str(AVAL)],['documento_persona','documento_condicional'])
        self.assertFalse(next(r for r in rows if r['participant_id']==str(REP) and r['code']=='documento_opcional')['required'])

    def test_marriage_condition_is_unknown_until_answered_and_case_insensitive(self):
        for value,expected in ((None,None),('CASADO',True),('Soltero',False)):
            deps={(str(AVAL),'estado_civil'):value} if value else {}
            row=next(r for r in d.requirements(self.people,deps) if r['code']=='documento_condicional')
            self.assertIs(row['applicable'],expected)

    def test_formats_size_and_unconfigured_storage_fail_closed(self):
        self.assertEqual(d.inspect_file(b'%PDF-1.4\n'),('application/pdf','pdf'))
        self.assertEqual(d.inspect_file(bytes([255,216,255,1])),('image/jpeg','jpg'))
        with self.assertRaises(ValueError):d.inspect_file(b'<html>hello</html>')
        with self.assertRaises(ValueError):d.inspect_file(b'%PDF-'+b'x'*d.MAX_BYTES)
        with patch.dict('os.environ',{'N8N_DOCUMENTS_WEBHOOK_URL':'https://other.test/upload','N8N_DOCUMENTS_WEBHOOK_TOKEN':'test'}):
            self.assertFalse(d.upload_configured())
            with self.assertRaises(d.DocumentUnavailable):d.store_in_sharepoint(b'%PDF-',{})

    def test_owner_check_happens_before_document_lookup(self):
        def deny(*args,**kwargs):raise HTTPException(404,'Captura no encontrada')
        routes,conn=self.routes(authorize=deny)
        with self.assertRaises(HTTPException):routes['/intakes/{intake_id}/documents'](INTAKE,None)
        self.assertEqual(conn.calls,[])

    def test_defer_preserves_required_pending_and_reuse_is_offered_only_same_rfc(self):
        conn=FakeConn();conn.states=[{'participant_id':AVAL,'document_code':'documento_persona','status':'received','upload_id':UPLOAD}]
        routes,_=self.routes(conn)
        checklist=routes['/intakes/{intake_id}/documents'](INTAKE,None)
        rep_ine=next(r for r in checklist['documents'] if r['participant_id']==str(REP) and r['code']=='documento_persona')
        self.assertEqual(rep_ine['reuse_sources'][0]['participant_id'],str(AVAL))
        self.assertGreater(checklist['required_missing'],0)
        out=routes['/intakes/{intake_id}/documents/{participant_id}/{code}/defer'](INTAKE,APP,'documento_empresa',None)
        self.assertTrue(out['ok'])
        self.assertTrue(any(params[3]=='deferred' for query,params in conn.calls if query.startswith('INSERT INTO document_states')))

    def test_reuse_rejects_cross_intake_source_and_other_rfc(self):
        routes,conn=self.routes()
        source=document_routes.ReuseDocumentInput(source_participant_id=UPLOAD)
        with self.assertRaises(HTTPException):routes['/intakes/{intake_id}/documents/{participant_id}/{code}/reuse'](INTAKE,REP,'documento_persona',source,None)
        self.assertFalse(any(q.startswith('INSERT INTO document_states') for q,_ in conn.calls))
        other=copy.deepcopy(self.people);other[str(AVAL)]['rfc']='EFGH020202AB1'
        routes,conn=self.routes(people=other)
        source=document_routes.ReuseDocumentInput(source_participant_id=AVAL)
        with self.assertRaises(HTTPException):routes['/intakes/{intake_id}/documents/{participant_id}/{code}/reuse'](INTAKE,REP,'documento_persona',source,None)
        self.assertFalse(any(q.startswith('INSERT INTO document_states') for q,_ in conn.calls))

    def test_reuse_links_existing_upload_and_does_not_copy_binary(self):
        conn=FakeConn();conn.source_upload={'upload_id':UPLOAD}
        routes,_=self.routes(conn)
        source=document_routes.ReuseDocumentInput(source_participant_id=AVAL)
        out=routes['/intakes/{intake_id}/documents/{participant_id}/{code}/reuse'](INTAKE,REP,'documento_persona',source,None)
        self.assertTrue(out['ok'])
        assignment=next(params for query,params in conn.calls if query.startswith('INSERT INTO document_states'))
        self.assertEqual(assignment,(INTAKE,REP,'documento_persona','received',UPLOAD))

    def test_completed_upload_retry_does_not_send_the_file_again(self):
        import hashlib
        content=b'%PDF-1.4\n'
        conn=FakeConn();conn.previous={'id':UPLOAD,'intake_id':INTAKE,'participant_id':APP,'document_code':'documento_empresa','sha256':hashlib.sha256(content).hexdigest(),'status':'received','updated_at':datetime.now(timezone.utc)}
        routes,_=self.routes(conn)
        class Request:
            async def stream(self):yield content
        with patch.object(d,'upload_configured',return_value=True),patch.object(d,'store_in_sharepoint') as store:
            out=asyncio.run(routes['/intakes/{intake_id}/documents/{participant_id}/{code}/uploads/{upload_id}'](INTAKE,APP,'documento_empresa',UPLOAD,Request()))
        self.assertEqual(out['status'],'received');store.assert_not_called()

    def test_successful_upload_stores_metadata_only_after_storage_confirmation(self):
        routes,conn=self.routes()
        class Request:
            async def stream(self):yield b'%PDF-1.4\n'
        with patch.object(d,'upload_configured',return_value=True),patch.object(d,'store_in_sharepoint',return_value='sharepoint-test-id') as store:
            out=asyncio.run(routes['/intakes/{intake_id}/documents/{participant_id}/{code}/uploads/{upload_id}'](INTAKE,APP,'documento_empresa',UPLOAD,Request()))
        self.assertEqual(out['status'],'received');store.assert_called_once()
        metadata=store.call_args.args[1]
        self.assertEqual(metadata['rfc'],self.people[str(APP)]['rfc'])
        self.assertEqual(metadata['file_name'],f'ABC010101AB1-documento-empresa-por-revisar-{UPLOAD}.pdf')
        self.assertTrue(any("SET status='received'" in query for query,_ in conn.calls))
        self.assertFalse(any(isinstance(value,bytes) for _,params in conn.calls for value in params))

    def test_upload_sends_the_participants_rfc_instead_of_the_applicants(self):
        routes,_=self.routes()
        class Request:
            async def stream(self):yield b'%PDF-1.4\n'
        with patch.object(d,'upload_configured',return_value=True),patch.object(d,'store_in_sharepoint',return_value='sharepoint-test-id') as store:
            asyncio.run(routes['/intakes/{intake_id}/documents/{participant_id}/{code}/uploads/{upload_id}'](INTAKE,REP,'documento_persona',UPLOAD,Request()))
        self.assertEqual(store.call_args.args[1]['rfc'],self.people[str(REP)]['rfc'])
        self.assertTrue(store.call_args.args[1]['file_name'].startswith('ABCD010101AB1-documento-persona-por-revisar-'))

    def test_failed_storage_leaves_the_requirement_pending(self):
        routes,conn=self.routes()
        class Request:
            async def stream(self):yield b'%PDF-1.4\n'
        with patch.object(d,'upload_configured',return_value=True),patch.object(d,'store_in_sharepoint',side_effect=d.DocumentUnavailable('test')):
            with self.assertRaises(HTTPException) as error:asyncio.run(routes['/intakes/{intake_id}/documents/{participant_id}/{code}/uploads/{upload_id}'](INTAKE,APP,'documento_empresa',UPLOAD,Request()))
        self.assertEqual(error.exception.status_code,503)
        self.assertFalse(any(q.startswith('INSERT INTO document_states') for q,_ in conn.calls))

if __name__=='__main__':unittest.main()
