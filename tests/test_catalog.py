import asyncio
import copy
import os
import unittest
from contextlib import nullcontext
from io import BytesIO
from unittest.mock import patch
from fastapi import FastAPI,HTTPException
from openpyxl import load_workbook
from app import catalog,conversation as c,documents
from app.catalog_routes import install

DOC={'scope':'PM','role':'Solicitante','code':'documento_ficticio','label':'Documento ficticio','order':1,'required':True,'dependency_field':None,'dependency_value':None}

def config():
    with patch.object(documents,'CATALOG',[DOC]):return catalog.baseline()

def person(configured=None):
    p={'id':'company','role':'solicitante','subject_type':'PM','rfc':'ABC010101AB1','answers':{'razon_social':'Empresa ficticia'}}
    if configured is not None:p['catalog']=configured
    return p

class Conn:
    def __init__(self):self.calls=[];self.query='';self.version=0;self.body=None;self.saved=None
    def execute(self,query,params=()):self.query=query;self.calls.append((query,params));return self
    def fetchone(self):
        if 'SELECT email' in self.query:return {'email':'admin@example.test'}
        if 'ORDER BY id DESC' in self.query:return {'id':self.version,'body':self.body} if self.version else None
        if 'INSERT INTO capture_catalogs' in self.query:self.version+=1;self.body=self.calls[-1][1][1].obj;return {'id':self.version}
        if 'SELECT catalog_snapshot' in self.query:return {'catalog_snapshot':self.saved}
        return None
    def fetchall(self):return []
    def transaction(self):return nullcontext()
class Pool:
    def __init__(self,conn):self.conn=conn
    def connection(self):return nullcontext(self.conn)
class Request:
    def __init__(self,body):self.body=body
    async def stream(self):yield self.body

class CatalogTests(unittest.TestCase):
    def routes(self,conn):
        app=FastAPI();install(app,Pool(conn),lambda request:'admin-id')
        return {r.path:r.endpoint for r in app.routes}
    def test_trade_name_reference_with_typo_and_explicit_reference_advances(self):
        for message in ['es iguak','es igual','sí, es la misma que la razón social']:
            people={'company':person(config())}
            proposal=c.local_reference(people,message,'company',[])
            result,audit,_=c.apply_proposal(people,proposal,message,'company')
            self.assertEqual(result['company']['answers']['nombre_comercial'],'Empresa ficticia')
            self.assertEqual(c.missing(result['company'])[0],'actividad')
            self.assertEqual(audit[0]['evidence'],message)
            self.assertNotIn('nombre_comercial',people['company']['answers'])
    def test_reference_does_not_copy_a_negative_statement_or_missing_source(self):
        people={'company':person(config())}
        self.assertIsNone(c.local_reference(people,'no es igual a la razón social','company',[]))
        people['company']['answers'].clear()
        self.assertIsNone(c.local_reference(people,'es igual','company',[]))
    def test_clarification_keeps_the_current_field_and_can_save_a_literal_answer(self):
        people={'company':person(config())};question='¿Del solicitante o del contacto?'
        context,_=c.context_for_turn(people,'company',[{'user':'es igual','assistant':question}],question)
        self.assertEqual(context['current_question']['field'],'nombre_comercial')
        progress=c.capture_progress(people);self.assertEqual(progress,{'completed':2,'total':7,'label':'Captura de datos'})
        proposal={'reply':'Seguimos','actions':[{'type':'save_field','target_id':'company','source_id':None,'role':None,'field':'pagina_web','value':'Empresa ficticia','evidence':'Empresa ficticia'}]}
        aligned,_=c.align_direct_answer(proposal,people,'Empresa ficticia','company',question)
        self.assertEqual(aligned['actions'][0]['field'],'nombre_comercial')
    def test_dynamic_field_order_and_question_apply_only_to_the_snapshot(self):
        old=config();new=copy.deepcopy(old)
        new['fields'].append({'role':'solicitante','subject_type':'PM','code':'ventas_anuales','label':'Ventas anuales','question':'¿Cuáles son sus ventas anuales?','type':'text','order':1,'enabled':True,'reuse_from':'','options':[]})
        new=catalog.validate(new)
        old_person=person(old);new_person=person(new)
        self.assertNotIn('ventas_anuales',catalog.fields_for(old_person));self.assertIn('ventas_anuales',catalog.fields_for(new_person))
        self.assertEqual(c.missing(new_person)[0],'ventas_anuales')
        proposal={'reply':'Seguimos','actions':[{'type':'save_field','target_id':'company','source_id':None,'role':None,'field':'ventas_anuales','value':'1000000','evidence':'1000000'}]}
        result,_,_=c.apply_proposal({'company':new_person},proposal,'1000000','company')
        self.assertEqual(result['company']['answers']['ventas_anuales'],'1000000')
        with self.assertRaises(c.InvalidProposal):c.apply_proposal({'company':old_person},proposal,'1000000','company')
    def test_disable_a_question_and_validate_selection_options(self):
        cfg=config();row=next(r for r in cfg['fields'] if r['role']=='solicitante' and r['subject_type']=='PM' and r['code']=='actividad');row.update(type='select',options=['Comercio','Servicios'])
        p=person(catalog.validate(cfg));catalog.validate_value(p,'actividad','servicios')
        with self.assertRaises(ValueError):catalog.validate_value(p,'actividad','Otro')
        row['enabled']=False
        self.assertNotIn('actividad',catalog.fields_for(person(catalog.validate(cfg))))
    def test_catalog_rejects_duplicate_rfc_unknown_types_and_invalid_reuse(self):
        for mode in ['duplicate','rfc','type','reuse']:
            cfg=config()
            if mode=='duplicate':cfg['fields'].append(copy.deepcopy(cfg['fields'][0]))
            if mode=='rfc':cfg['fields'][0]['code']='rfc'
            if mode=='type':cfg['fields'][0]['type']='script'
            if mode=='reuse':cfg['fields'][0]['reuse_from']='missing'
            with self.assertRaises(ValueError):catalog.validate(cfg)
    def test_excel_roundtrip_and_formula_rejection(self):
        cfg=config();payload=catalog.workbook(cfg)
        self.assertEqual(catalog.parse_workbook(payload),catalog.validate(cfg))
        book=load_workbook(BytesIO(payload));book['Preguntas']['E2']='=1+1';out=BytesIO();book.save(out)
        with self.assertRaises(ValueError):catalog.parse_workbook(out.getvalue())
        with self.assertRaises(ValueError):catalog.parse_workbook(b'not an excel')
    def test_document_requirements_use_the_request_snapshot(self):
        cfg=config();people={'company':person(cfg)}
        with patch.object(documents,'CATALOG',[]):self.assertEqual(documents.requirements(people)[0]['code'],'documento_ficticio')
        cfg['documents'][0]['required']=False
        self.assertFalse(documents.requirements(people)[0]['required'])
    def test_administrator_access_is_deny_by_default(self):
        conn=Conn();routes=self.routes(conn)
        with patch.dict(os.environ,{'CAPTURE_CONFIG_ADMIN_EMAILS':''}):
            with self.assertRaises(HTTPException) as e:asyncio.run(routes['/admin/catalog/preview'](Request(b'{}')))
        self.assertEqual(e.exception.status_code,403)
        self.assertFalse(any(q.startswith('UPDATE') or q.startswith('INSERT') for q,_ in conn.calls))
    def test_publish_freezes_legacy_snapshots_and_prevents_stale_publications(self):
        import json
        conn=Conn();routes=self.routes(conn)
        body=json.dumps({'base_version':0,'config':config()}).encode()
        with patch.dict(os.environ,{'CAPTURE_CONFIG_ADMIN_EMAILS':'admin@example.test'}),patch.object(catalog,'baseline',return_value=config()):
            out=asyncio.run(routes['/admin/catalog/publish'](Request(body)))
            self.assertEqual(out['version'],1)
            self.assertTrue(any('catalog_snapshot IS NULL' in q for q,_ in conn.calls))
            with self.assertRaises(HTTPException) as e:asyncio.run(routes['/admin/catalog/publish'](Request(body)))
            self.assertEqual(e.exception.status_code,409)
            conn.saved=config();old=catalog.snapshot(conn,'old');conn.body['fields'][0]['question']='Nueva pregunta';new=catalog.active(conn)[1]
            self.assertNotEqual(old['fields'][0]['question'],new['fields'][0]['question'])
