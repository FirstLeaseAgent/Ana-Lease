import copy
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from uuid import UUID
from app import conversation as c,memory,handoffs,main

def company():
    return {'id':'company','role':'solicitante','subject_type':'PM','rfc':'ABC010101AB1',
            'answers':{**{f:'Dato ficticio' for f in c.FIELDS['solicitante']['PM']},
                       'telefono':'5551234567','correo_contacto':'prueba@example.test'}}

def contact():
    return {'id':'contact','role':'contacto','subject_type':'PF','rfc':None,
            'answers':{'nombre':'Nombre Ficticio Rincón','correo_contacto':'prueba@example.test','telefono':'5551234567'}}

class HandoffTests(unittest.TestCase):
    def people(self):return {'company':company(),'contact':contact()}

    def test_transcript_email_then_equal_phone_reuses_only_phone_without_identity(self):
        people=self.people();people['contact']['answers'].pop('telefono');people['contact']['answers'].pop('correo_contacto')
        message='el mismo del solicitante'
        proposal=c.local_reference(people,message,'contact',[])
        people,audit,_=c.apply_proposal(people,proposal,message,'contact')
        relations=memory.relations_from_actions(people,audit)
        history=c.history_for_turns([{'user_message':message,'response_json':{'reply':c.resume_reply(people,'contact')},'audit_json':audit}],people)
        proposal=c.local_reference(people,'igual','contact',history,relations)
        self.assertEqual(proposal['actions'][0]['field'],'telefono')
        self.assertEqual(proposal['actions'][0]['source_id'],'company')
        updated,audit,_=c.apply_proposal(people,proposal,'igual','contact')
        self.assertEqual(updated['contact']['answers']['telefono'],'5551234567')
        self.assertEqual(c.identity_audit(updated,audit,'igual'),[])
        self.assertTrue(all(r['type']=='shared_field' for r in memory.relations_from_actions(updated,audit)))

    def test_retry_after_repeated_equal_keeps_saved_channel_reference(self):
        people=self.people();people['contact']['answers'].pop('telefono')
        relations=memory.relations_from_actions(people,[{'type':'reuse_field','target_id':'contact','source_id':'company','field':'correo_contacto'}])
        history=[{'user':'el mismo del solicitante','capture_targets':[{'id':'contact','fields':['correo_contacto']}]},
                 {'user':'igual','capture_targets':[]}]
        self.assertTrue(c.local_reference(people,'igual','contact',history,relations)['actions'])

    def test_equal_with_no_prior_reference_does_not_copy_a_phone(self):
        people=self.people();people['contact']['answers'].pop('telefono')
        self.assertIsNone(c.local_reference(people,'igual','contact',[],[]))

    def test_intervening_negative_instruction_invalidates_channel_carry(self):
        people=self.people();people['contact']['answers'].pop('telefono')
        relations=memory.relations_from_actions(people,[{'type':'reuse_field','target_id':'contact','source_id':'company','field':'correo_contacto'}])
        history=[{'user':'el mismo del solicitante','capture_targets':[{'id':'contact','fields':['correo_contacto']}]},
                 {'user':'el teléfono no es del solicitante','capture_targets':[]}]
        self.assertIsNone(c.local_reference(people,'igual','contact',history,relations))

    def test_shared_session_email_does_not_supply_a_phone(self):
        people=self.people();people['contact']['answers'].pop('telefono')
        history=[{'user':'mi correo','capture_targets':[{'id':'contact','fields':['correo_contacto']}]}]
        relations=memory.relations_from_actions(people,[{'type':'reuse_field','target_id':'contact','source_id':'session_user','field':'correo_contacto'}],'prueba@example.test')
        self.assertIsNone(c.local_reference(people,'igual','contact',history,relations))

    def test_same_channel_from_ambiguous_role_asks_without_action(self):
        people=self.people();people['contact']['answers'].pop('telefono')
        for i in range(2):people['aval'+str(i)]={'id':'aval'+str(i),'role':'aval','subject_type':'PF','rfc':'ABCD010101AB'+str(i),'answers':{f:'Dato ficticio' for f in c.FIELDS['aval']['PF']}}
        proposal=c.local_reference(people,'el mismo del aval','contact',[])
        self.assertEqual(proposal['actions'],[])

    def test_transcript_full_contact_name_starts_rep_and_requests_missing_rfc(self):
        people=self.people();message='Nombre Ficticio Rincón'
        self.assertTrue(c.pending_representative(people))
        proposal=c.local_reference(people,message,None,[])
        updated,audit,pid=c.apply_proposal(people,proposal,message)
        self.assertEqual(updated[pid]['role'],'representante')
        self.assertEqual(updated[pid]['answers'],people['contact']['answers'])
        self.assertIsNone(updated[pid]['rfc'])
        self.assertEqual(c.missing(updated[pid]),['rfc','cargo'])
        self.assertTrue(c.identity_audit(updated,audit,message))
        self.assertIn('RFC',c.resume_reply(updated,pid))

    def test_full_name_matching_ignores_case_accents_and_extra_spaces(self):
        people=self.people();message='NOMBRE  FICTICIO RINCON'
        updated,audit,pid=c.apply_proposal(people,c.local_reference(people,message,None,[]),message)
        self.assertEqual(updated[pid]['answers']['nombre'],'Nombre Ficticio Rincón')
        self.assertTrue(c.identity_audit(updated,audit,message))

    def test_implicit_same_contact_role_in_pending_rep_transition(self):
        people=self.people();message='es el mismo contacto'
        updated,audit,pid=c.apply_proposal(people,c.local_reference(people,message,None,[]),message)
        self.assertEqual(updated[pid]['role'],'representante')
        self.assertEqual(updated[pid]['answers']['nombre'],'Nombre Ficticio Rincón')
        self.assertTrue(c.identity_audit(updated,audit,message))

    def test_new_full_name_creates_rep_without_copying_unrelated_contact_fields(self):
        people=self.people();message='Otra Persona Ficticia'
        updated,_,pid=c.apply_proposal(people,c.local_reference(people,message,None,[]),message)
        self.assertEqual(updated[pid]['answers'],{'nombre':message})
        self.assertIsNone(updated[pid]['rfc'])
        self.assertEqual(c.missing(updated[pid]),['rfc','cargo','correo_contacto','telefono'])

    def test_full_name_is_not_an_implicit_role_outside_pending_rep_phase(self):
        people=self.people();people['company']['answers'].pop('actividad')
        self.assertIsNone(c.local_reference(people,'Nombre Ficticio Rincón','company',[]))
        forged={'reply':'Listo','actions':[{'type':'add_participant','target_id':'new','source_id':None,'role':'representante','field':None,'value':None,'evidence':'Nombre Ficticio Rincón'}]}
        with self.assertRaises(c.InvalidProposal):c.apply_proposal(people,forged,'Nombre Ficticio Rincón')

    def test_ambiguous_existing_full_name_does_not_create_or_copy(self):
        people=self.people();people['aval']={'id':'aval','role':'aval','subject_type':'PF','rfc':'ABCD010101AB1',
                                           'answers':{**{f:'Dato ficticio' for f in c.FIELDS['aval']['PF']},'nombre':'Nombre Ficticio Rincón'}}
        proposal=c.local_reference(people,'Nombre Ficticio Rincón',None,[])
        self.assertEqual(proposal['actions'],[])
        self.assertIn('más de un',proposal['reply'])

    def test_ok_repeats_specific_question_without_claiming_creation(self):
        people=self.people();proposal=c.local_reference(people,'ok',None,[])
        self.assertEqual(proposal['actions'],[])
        self.assertIn('nombre completo',proposal['reply'])
        self.assertIn('es el mismo contacto',proposal['reply'])

    def test_question_or_uncertainty_is_not_a_name(self):
        for text in ['no sé','quiero terminar','¿Quién puede ser?','mañana seguimos','Hola gracias','listo accionistas','sin avales']:
            with self.subTest(text=text):self.assertFalse(c.implicit_representative_input(self.people(),text))

    def test_a_name_cannot_implicitly_add_an_aval_or_shareholder(self):
        people=self.people();message='Otra Persona Ficticia'
        for role in ['aval','accionista']:
            proposal={'reply':'Listo','actions':[{'type':'add_participant','target_id':'new','source_id':None,'role':role,'field':None,'value':None,'evidence':message}]}
            with self.subTest(role=role),self.assertRaises(c.InvalidProposal):c.apply_proposal(people,proposal,message)

    def test_role_transition_route_commits_name_reuse_and_identity_without_model(self):
        people=self.people()
        class Conn:
            def __init__(self):self.calls=[];self.query=''
            def execute(self,q,p=()):self.query=q;self.calls.append((q,p));return self
            def fetchone(self):
                if 'SELECT * FROM capture_turns' in self.query:return None
                if 'sum(t.attempts)' in self.query:return {'n':0}
                if 'SELECT email' in self.query:return {'email':'session@example.test'}
                raise AssertionError(self.query)
            def fetchall(self):return []
            def transaction(self):return nullcontext()
        conn=Conn();iid=UUID('00000000-0000-4000-8000-000000000001')
        body=main.ConversationInput(request_id=UUID('00000000-0000-4000-8000-000000000002'),message='Nombre Ficticio Rincón',question=c.resume_reply(people))
        with patch.object(main,'owner',return_value='owner'),patch.object(main,'intake_for_owner',return_value={'capture_active_id':None,'capture_version':1}),patch.object(main,'conversation_people',return_value=people),patch.object(main,'conversation_enabled_for',return_value=True),patch.object(main.pool,'connection',return_value=nullcontext(conn)),patch.object(main,'call_agent') as agent:
            response=main.converse(iid,body,None)
        agent.assert_not_called()
        saved=next(p for q,p in conn.calls if q.startswith("UPDATE capture_turns SET status='complete'"))
        self.assertEqual(saved[1].obj[-1]['type'],'confirm_identity')
        self.assertIn('RFC',response['reply'])
        self.assertNotIn('Nombre Ficticio Rincón',response['reply'])

if __name__=='__main__':unittest.main()
