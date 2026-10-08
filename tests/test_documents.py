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
    def __init__(self):self.calls=[];self.query='';self.states=[];self.previous=None;self.source_upload=None;self.processing=False
    def transaction(self):return nullcontext()
    def execute(self,query,params=()):self.query=query;self.calls.append((query,params));return self
    def fetchall(self):return self.states if 'FROM document_states' in self.query else []
    def fetchone(self):
        if "status='processing'" in self.query:return {'processing':True} if self.processing else None
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
        document_routes.install(app,FakePool(conn),lambda request:'owner',authorize or (lambda *args,**kwargs:{'status':'open'}),lambda *args:people or self.people,lambda *args:True)
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

    def test_shared_rfc_documents_are_requested_and_counted_once(self):
        routes,_=self.routes()
        out=routes['/intakes/{intake_id}/documents'](INTAKE,None)
        self.assertEqual(len(out['documents']),4)
        self.assertEqual(out['required_missing'],3)
        shared=next(r for r in out['documents'] if r['code']=='documento_persona')
        self.assertEqual(len(shared['members']),2)
        self.assertIn('Representante',shared['participant'])
        self.assertIn('Aval',shared['participant'])

    def test_grouping_keeps_distinct_missing_rfcs_and_document_codes_separate(self):
        for rfc in ('EFGH020202AB1','',None):
            people=copy.deepcopy(self.people);people[str(AVAL)]['rfc']=rfc
            groups=d.grouped_requirements(d.requirements(people),people)
            self.assertEqual(len(groups),5)
        groups=d.grouped_requirements(d.requirements(self.people),self.people)
        self.assertEqual({r['code'] for r in groups},{'documento_empresa','documento_persona','documento_opcional','documento_condicional'})

    def test_optional_role_cannot_make_a_shared_required_document_optional(self):
        catalog=copy.deepcopy(d.CATALOG)
        next(r for r in catalog if r['role']=='RepresentanteLegal' and r['code']=='documento_persona')['required']=False
        with patch.object(d,'CATALOG',catalog):
            groups=d.grouped_requirements(d.requirements(self.people),self.people)
        self.assertTrue(next(r for r in groups if r['code']=='documento_persona')['required'])

    def test_unresolved_condition_cannot_use_another_roles_received_file(self):
        catalog=copy.deepcopy(d.CATALOG)
        row=next(r for r in catalog if r['role']=='Aval' and r['code']=='documento_persona')
        row.update(dependency_field='estado_civil',dependency_value='Casado')
        states=[{'participant_id':REP,'document_code':'documento_persona','status':'received','upload_id':UPLOAD}]
        with patch.object(d,'CATALOG',catalog):
            groups=d.grouped_requirements(d.requirements(self.people),self.people,states)
        shared=[r for r in groups if r['code']=='documento_persona']
        self.assertEqual(len(shared),2)
        self.assertEqual([r['status'] for r in shared],['received','pending'])
        self.assertIs(shared[1]['applicable'],None)

    def test_upload_covers_all_matching_roles_with_one_storage_call(self):
        routes,conn=self.routes()
        class Request:
            async def stream(self):yield b'%PDF-1.4\n'
        with patch.object(d,'upload_configured',return_value=True),patch.object(d,'store_in_sharepoint',return_value='sharepoint-test-id') as store:
            asyncio.run(routes['/intakes/{intake_id}/documents/{participant_id}/{code}/uploads/{upload_id}'](INTAKE,REP,'documento_persona',UPLOAD,Request()))
        store.assert_called_once()
        assignments=[params for query,params in conn.calls if query.startswith('INSERT INTO document_states')]
        self.assertEqual({str(p[1]) for p in assignments},{str(REP),str(AVAL)})
        self.assertTrue(all(p[4]==UPLOAD for p in assignments))

    def test_defer_covers_group_and_cannot_hide_a_received_other_role(self):
        routes,conn=self.routes()
        routes['/intakes/{intake_id}/documents/{participant_id}/{code}/defer'](INTAKE,REP,'documento_persona',None)
        assignments=[params for query,params in conn.calls if query.startswith('INSERT INTO document_states')]
        self.assertEqual({str(p[1]) for p in assignments},{str(REP),str(AVAL)})
        conn=FakeConn();conn.states=[{'participant_id':AVAL,'document_code':'documento_persona','status':'received','upload_id':UPLOAD}]
        routes,_=self.routes(conn)
        with self.assertRaises(HTTPException):routes['/intakes/{intake_id}/documents/{participant_id}/{code}/defer'](INTAKE,REP,'documento_persona',None)
        self.assertFalse(any(q.startswith('INSERT INTO document_states') for q,_ in conn.calls))

    def test_finalize_accepts_preexisting_file_from_one_role_and_links_other(self):
        people=copy.deepcopy(self.people);people[str(AVAL)]['answers']['estado_civil']='Soltero'
        routes,conn=self.routes(people=people)
        conn.states=[{'participant_id':APP,'document_code':'documento_empresa','status':'received','upload_id':UPLOAD},
                     {'participant_id':AVAL,'document_code':'documento_persona','status':'received','upload_id':UPLOAD}]
        out=routes['/intakes/{intake_id}/finalize'](INTAKE,None)
        self.assertEqual(out['status'],'submitted')
        assignments=[params for query,params in conn.calls if query.startswith('INSERT INTO document_states')]
        self.assertEqual(assignments,[(INTAKE,str(REP),'documento_persona','received',UPLOAD)])

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
        self.assertEqual(rep_ine['status'],'received')
        self.assertEqual({m['participant_id'] for m in rep_ine['members']},{str(REP),str(AVAL)})
        self.assertEqual(rep_ine['reuse_sources'],[])
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

    def test_finalize_accepts_missing_and_unknown_conditions_and_records_followup(self):
        routes,conn=self.routes()
        out=routes['/intakes/{intake_id}/finalize'](INTAKE,None)
        self.assertEqual(out['status'],'submitted')
        details=next(params[5].obj for q,params in conn.calls if q.startswith('INSERT INTO document_events') and params[4]=='finalize')
        self.assertEqual(details['required_missing'],3)
        self.assertEqual(len(details['pending_documents']),3)
        self.assertEqual({d['code'] for d in details['pending_documents']},{'documento_empresa','documento_persona','documento_condicional'})
        self.assertIs(next(d for d in details['pending_documents'] if d['code']=='documento_condicional')['applicable'],None)
        self.assertFalse(any(q.startswith('INSERT INTO document_states') for q,_ in conn.calls))
        conn=FakeConn();conn.states=[{'participant_id':UUID(row['participant_id']),'document_code':row['code'],'status':'received'} for row in d.requirements(self.people)]
        routes,_=self.routes(conn)
        self.assertEqual(routes['/intakes/{intake_id}/finalize'](INTAKE,None)['status'],'submitted')
        details=next(params[5].obj for q,params in conn.calls if q.startswith('INSERT INTO document_events') and params[4]=='finalize')
        self.assertEqual(details['required_missing'],1)
        self.assertEqual(details['pending_documents'][0]['code'],'documento_condicional')

    def test_submitted_checklist_keeps_missing_documents_visible(self):
        routes,_=self.routes(authorize=lambda *args,**kwargs:{'status':'submitted'})
        out=routes['/intakes/{intake_id}/documents'](INTAKE,None)
        self.assertEqual(out['required_missing'],3)
        self.assertIn('documentos pendientes',out['message'])
        self.assertEqual(next(r for r in out['documents'] if r['code']=='documento_persona')['status'],'pending')

    def test_finalize_still_waits_for_inflight_storage_and_capture(self):
        conn=FakeConn();conn.processing=True
        routes,_=self.routes(conn)
        with self.assertRaises(HTTPException) as error:routes['/intakes/{intake_id}/finalize'](INTAKE,None)
        self.assertEqual(error.exception.status_code,409)
        self.assertFalse(any("status='submitted'" in q for q,_ in conn.calls))
        people=copy.deepcopy(self.people);people[str(REP)]['answers'].pop('cargo')
        routes,conn=self.routes(people=people)
        with self.assertRaises(HTTPException):routes['/intakes/{intake_id}/finalize'](INTAKE,None)
        self.assertFalse(any("status='submitted'" in q for q,_ in conn.calls))

    def test_finalize_accepts_complete_required_documents_and_audits_once(self):
        people=copy.deepcopy(self.people);people[str(AVAL)]['answers']['estado_civil']='Soltero'
        routes,conn=self.routes(people=people)
        conn.states=[{'participant_id':UUID(row['participant_id']),'document_code':row['code'],'status':'received'} for row in d.requirements(people) if row['required'] and row['applicable'] is True]
        out=routes['/intakes/{intake_id}/finalize'](INTAKE,None)
        self.assertEqual(out['status'],'submitted')
        self.assertEqual(sum("status='submitted'" in q for q,_ in conn.calls),1)
        self.assertEqual(sum(q.startswith('INSERT INTO document_events') for q,_ in conn.calls),1)
        routes,conn=self.routes(authorize=lambda *args,**kwargs:{'status':'submitted'})
        self.assertEqual(routes['/intakes/{intake_id}/finalize'](INTAKE,None)['status'],'submitted')
        self.assertFalse(any(q.startswith('UPDATE') or q.startswith('INSERT') for q,_ in conn.calls))
        checklist=routes['/intakes/{intake_id}/documents'](INTAKE,None)
        self.assertEqual(checklist['status'],'submitted');self.assertFalse(checklist['upload_available'])

    def test_submitted_owner_can_supply_missing_file_and_cannot_replace_received(self):
        routes,conn=self.routes(authorize=lambda *args,**kwargs:{'status':'submitted'})
        class Request:
            async def stream(self):yield b'%PDF-1.4\n'
        with patch.object(d,'upload_configured',return_value=True),patch.object(d,'store_in_sharepoint',return_value='late-file') as store:
            self.assertTrue(routes['/intakes/{intake_id}/documents'](INTAKE,None)['upload_available'])
            out=asyncio.run(routes['/intakes/{intake_id}/documents/{participant_id}/{code}/uploads/{upload_id}'](INTAKE,REP,'documento_persona',UPLOAD,Request()))
            self.assertEqual(out['status'],'received')
            store.assert_called_once()
        conn.states=[{'participant_id':AVAL,'document_code':'documento_persona','status':'received','upload_id':UPLOAD}]
        with patch.object(d,'store_in_sharepoint') as store:
            with self.assertRaises(HTTPException) as error:
                asyncio.run(routes['/intakes/{intake_id}/documents/{participant_id}/{code}/uploads/{upload_id}'](INTAKE,REP,'documento_persona',UPLOAD,Request()))
            self.assertEqual(error.exception.status_code,409)
            store.assert_not_called()

    def test_finalize_checks_ownership_before_reading_or_writing(self):
        def deny(*args,**kwargs):raise HTTPException(404,'Captura no encontrada')
        routes,conn=self.routes(authorize=deny)
        with self.assertRaises(HTTPException):routes['/intakes/{intake_id}/finalize'](INTAKE,None)
        self.assertEqual(conn.calls,[])

if __name__=='__main__':unittest.main()
