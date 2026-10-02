import copy
import unittest
from unittest.mock import patch
from contextlib import nullcontext
from uuid import UUID
from app import conversation as c,catalog,shareholders,documents,main

CURP='AACD010101HDFRRL01'
RFC='ABCD010101AB1'

def company():
    return {'id':'company','role':'solicitante','subject_type':'PM','rfc':'ABC010101AB1',
            'answers':{f:'Dato ficticio' for f in c.FIELDS['solicitante']['PM']},
            'shareholders_enabled':True,'shareholders_complete':False,'guarantors_complete':False}

def representative():
    return {'id':'rep','role':'representante','subject_type':'PF','rfc':RFC,
            'answers':{'nombre':'Persona ficticia','cargo':'Director','telefono':'5551234567','correo_contacto':'ficticio@example.test'}}

def turn(people,message,preferred=None):
    proposal=c.local_reference(people,message,preferred,[])
    if proposal is None:raise AssertionError('Unexpected API call: '+message)
    return c.apply_proposal(people,proposal,message,preferred)

class ShareholderTests(unittest.TestCase):
    def people(self):return {'company':company(),'rep':representative()}

    def test_pf_complete_capture_and_explicit_closure_then_optional_guarantor(self):
        people=self.people()
        self.assertEqual(c.capture_stage(people),'shareholders')
        people,audit,pid=turn(people,'accionista')
        self.assertEqual(people[pid]['company_id'],'company')
        for answer in [RFC,'Persona ficticia',CURP,'60%']:
            people,audit,pid=turn(people,answer,pid)
        self.assertIsNone(pid)
        self.assertEqual(c.capture_stage(people),'shareholders')
        people,audit,pid=turn(people,'listo accionistas')
        self.assertEqual(audit[0]['type'],'finish_shareholders')
        self.assertEqual(c.capture_stage(people),'guarantors')
        people,_,_=turn(people,'sin aval')
        self.assertEqual(c.capture_stage(people),'documents')

    def test_pm_shareholder_does_not_require_curp(self):
        people=self.people();people,_,pid=turn(people,'accionista XYZ010101AB1')
        self.assertEqual(people[pid]['subject_type'],'PM')
        self.assertEqual(c.missing(people[pid]),['razon_social','porcentaje_participacion'])
        people,_,pid=turn(people,'Empresa ficticia',pid)
        people,_,pid=turn(people,'40',pid)
        self.assertIsNone(pid)

    def test_same_representative_copies_identity_but_not_role_data(self):
        people=self.people();people['rep']['answers']['curp']=CURP
        message='Agrega un accionista; es el mismo representante'
        updated,audit,pid=turn(people,message)
        self.assertEqual(updated[pid]['rfc'],RFC)
        self.assertEqual(updated[pid]['answers'],{'nombre':'Persona ficticia','curp':CURP})
        self.assertEqual(c.missing(updated[pid]),['porcentaje_participacion'])
        self.assertTrue(c.identity_audit(updated,audit,message))

    def test_missing_curp_is_asked_when_representative_has_none(self):
        people,_,pid=turn(self.people(),'Agrega un accionista; es el mismo representante')
        self.assertEqual(c.missing(people[pid]),['curp','porcentaje_participacion'])

    def test_cannot_close_with_pending_fields_or_before_representative(self):
        people=self.people();people,_,pid=turn(people,'accionista')
        proposal=c.local_reference(people,'listo accionistas',pid,[])
        self.assertEqual(proposal['actions'],[])
        people=self.people();del people['rep']
        self.assertEqual(c.local_reference(people,'listo accionistas',None,[])['actions'],[])
        self.assertEqual(c.capture_stage(people),'capture')

    def test_cannot_invent_closure_or_confirm_with_ok(self):
        people=self.people()
        self.assertEqual(c.local_reference(people,'ok',None,[])['actions'],[])
        forged={'reply':'Listo','actions':[shareholders.action('finish_shareholders',target='company',message='ok')]}
        with self.assertRaises(c.InvalidProposal):c.apply_proposal(people,forged,'ok')

    def test_sum_exceeding_100_is_atomic_and_different_companies_are_independent(self):
        people=self.people()
        for rfc,percent in [(RFC,'60'),('EFGH010101AB1','50')]:
            people,_,pid=turn(people,'accionista '+rfc)
            people,_,pid=turn(people,'Persona ficticia',pid)
            people,_,pid=turn(people,CURP,pid)
            if percent=='60':people,_,pid=turn(people,percent,pid)
            else:
                original=copy.deepcopy(people)
                with self.assertRaises(c.InvalidProposal):turn(people,percent,pid)
                self.assertEqual(people,original)
        other=self.people();other['company']['id']='other';other={'other':other['company'],'rep':other['rep']}
        other,_,pid=turn(other,'accionista '+RFC)
        self.assertEqual(other[pid]['company_id'],'other')

    def test_percent_and_curp_validation(self):
        for value in ['10','0','101','NaN','25 30','-25','25 por ciento']:
            with self.subTest(value=value),self.assertRaises(ValueError):shareholders.percentage(value)
        for value in ['10.01','25%','33,33','100']:self.assertGreater(shareholders.percentage(value),10)
        with self.assertRaises(ValueError):catalog.validate_value({'role':'accionista','subject_type':'PF','answers':{}},'curp','INVALIDA')

    def test_duplicate_shareholder_rfc_is_rejected_even_in_later_turn(self):
        people,_,pid=turn(self.people(),'accionista '+RFC)
        people,_,_=turn(people,'accionista')
        other=next(p['id'] for p in people.values() if p['role']=='accionista' and not p['rfc'])
        proposal={'reply':'Listo','actions':[shareholders.action('save_field',target=other,field='rfc',value=RFC,message=RFC)]}
        with self.assertRaises(c.InvalidProposal):c.apply_proposal(people,proposal,RFC)

    def test_three_slots_and_pf_applicant_rejection(self):
        people=self.people()
        for rfc in ['ABCD010101AB1','EFGH010101AB1','IJKL010101AB1']:people,_,_=turn(people,'accionista '+rfc)
        self.assertEqual(c.local_reference(people,'accionista',None,[])['actions'],[])
        people['company']['subject_type']='PF'
        self.assertEqual(c.local_reference(people,'accionista',None,[])['actions'],[])

    def test_old_snapshot_is_unchanged_and_new_role_gets_defaults(self):
        old={'fields':[r for r in catalog.baseline()['fields'] if r['role']!='accionista'],'documents':[]}
        people=self.people()
        for p in people.values():p['catalog']=old
        original=copy.deepcopy(old)
        updated,_,pid=turn(people,'accionista '+RFC)
        self.assertEqual(c.missing(updated[pid]),['nombre','curp','porcentaje_participacion'])
        self.assertEqual(old,original)

    def test_new_order_ignores_preferred_aval_until_prior_roles_finish(self):
        people=self.people();people['rep']['answers'].pop('cargo')
        people['aval']={'id':'aval','role':'aval','subject_type':'PF','rfc':RFC,'answers':{}}
        self.assertEqual(c.active_person(people,'aval'),'rep')
        people['rep']['answers']['cargo']='Director'
        self.assertIsNone(c.active_person(people,'aval'))
        people['company']['shareholders_complete']=True
        self.assertEqual(c.active_person(people,'aval'),'aval')

    def test_old_intake_keeps_previous_completion_flow(self):
        people=self.people();people['company']['shareholders_enabled']=False
        self.assertEqual(c.capture_stage(people),'documents')

    def test_documents_apply_to_actual_shareholder_and_person_type(self):
        people,_,pid=turn(self.people(),'accionista '+RFC)
        templates=[{'scope':'PF','role':'Accionista','code':'identificacion','label':'Identificación','order':1,'required':True,'dependency_field':None,'dependency_value':None}]
        with patch.object(documents,'CATALOG',templates):rows=documents.requirements(people)
        self.assertEqual(rows[0]['participant_id'],pid)

    def test_catalog_supports_shareholders_and_protects_core_requirements(self):
        doc={'scope':'PM','role':'Solicitante','code':'acta','label':'Acta','order':1,'required':True,'dependency_field':None,'dependency_value':None}
        with patch.object(documents,'CATALOG',[doc]):cfg=catalog.baseline()
        checked=catalog.validate(cfg)
        self.assertEqual(len([r for r in checked['fields'] if r['role']=='accionista']),5)
        next(r for r in cfg['fields'] if r['role']=='accionista' and r['code']=='porcentaje_participacion')['enabled']=False
        with self.assertRaises(ValueError):catalog.validate(cfg)

    def test_route_atomically_persists_company_relation_and_turn_without_ai(self):
        people=self.people()
        class Conn:
            def __init__(self):self.calls=[];self.query=''
            def execute(self,q,p=()):self.calls.append((q,p));self.query=q;return self
            def fetchone(self):
                if 'SELECT * FROM capture_turns' in self.query:return None
                if 'sum(t.attempts)' in self.query:return {'n':0}
                if 'SELECT email' in self.query:return {'email':'ficticio@example.test'}
                raise AssertionError(self.query)
            def fetchall(self):return []
            def transaction(self):return nullcontext()
        conn=Conn();iid=UUID('00000000-0000-4000-8000-000000000001')
        body=main.ConversationInput(request_id=UUID('00000000-0000-4000-8000-000000000002'),message='accionista')
        with patch.object(main,'owner',return_value='owner'),patch.object(main,'intake_for_owner',return_value={'capture_active_id':None,'capture_version':1}),patch.object(main,'conversation_people',return_value=people),patch.object(main,'conversation_enabled_for',return_value=True),patch.object(main.pool,'connection',return_value=nullcontext(conn)),patch.object(main,'call_agent') as agent:
            response=main.converse(iid,body,None)
        agent.assert_not_called()
        row=next(p for q,p in conn.calls if q.startswith('INSERT INTO participants'))
        self.assertEqual(row[-1],'company')
        self.assertEqual(row[2],'accionista')
        self.assertIn('RFC',response['reply'])

    def test_non_ai_completion_is_owner_scoped_and_persists_audit(self):
        people=self.people()
        class Conn:
            def __init__(self):self.calls=[]
            def execute(self,q,p=()):self.calls.append((q,p));return self
            def transaction(self):return nullcontext()
        conn=Conn();iid=UUID('00000000-0000-4000-8000-000000000001')
        with patch.object(main,'owner',return_value='owner'),patch.object(main,'intake_for_owner') as scope,patch.object(main,'conversation_people',return_value=people),patch.object(main.pool,'connection',return_value=nullcontext(conn)),patch.object(main,'call_agent') as ai:
            response=main.complete_shareholders(iid,None)
        scope.assert_called_once_with(conn,iid,'owner',editable=True)
        ai.assert_not_called()
        self.assertEqual(response['stage'],'guarantors')
        saved=next(p for q,p in conn.calls if q.startswith('INSERT INTO capture_turns'))
        self.assertEqual(saved[-1].obj[0]['type'],'finish_shareholders')

    def test_explicit_identity_conflict_cannot_overwrite_shareholder(self):
        people=self.people();people,_,pid=turn(people,'accionista '+RFC)
        people[pid]['answers']['nombre']='Otra persona'
        proposal=c.local_reference(people,'El accionista es el mismo representante',pid,[])
        self.assertEqual(proposal['actions'],[])
        self.assertIn('conflicto',proposal['reply'])

    def test_company_link_cannot_point_to_another_participant(self):
        people=self.people();people,_,pid=turn(people,'accionista '+RFC)
        people[pid]['company_id']='outside'
        with self.assertRaises(ValueError):shareholders.validate_people(people)

    def test_verified_curp_reference_does_not_copy_percentage(self):
        people=self.people();people['rep']['answers']['curp']=CURP
        people,_,pid=turn(people,'accionista '+RFC)
        people[pid]['answers']['nombre']='Persona ficticia'
        relations=c.relations_from_actions(people,[{'type':'confirm_identity','source_id':'rep','target_id':pid}])
        proposal=c.local_reference(people,'igual',pid,[],relations)
        updated,_,_=c.apply_proposal(people,proposal,'igual',pid)
        self.assertEqual(updated[pid]['answers']['curp'],CURP)
        self.assertNotIn('porcentaje_participacion',updated[pid]['answers'])

if __name__=='__main__':unittest.main()
