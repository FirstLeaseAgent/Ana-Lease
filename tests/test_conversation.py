import copy
import json
import os
import unittest
from contextlib import nullcontext
from uuid import UUID
from unittest.mock import patch

from app import conversation as c
from app import main


def person(pid, role, subject='PF', rfc=None, **answers):
    return {'id': pid, 'role': role, 'subject_type': subject, 'rfc': rfc, 'answers': answers}


def action(kind, target, field=None, value=None, source=None, role=None, evidence='El representante es el mismo contacto'):
    return dict(type=kind,target_id=target,source_id=source,role=role,field=field,value=value,evidence=evidence)


class ConversationTest(unittest.TestCase):
    def setUp(self):
        self.people = {'contact': person('contact','contacto',nombre='Nombre de prueba',correo_contacto='prueba@example.test',telefono='5551234567')}

    def test_saved_legal_name_cannot_be_asked_again_in_reply(self):
        people={'applicant':person('applicant','solicitante','PM',razon_social='Empresa de prueba')}
        proposal={'reply':'¿Cuál es la razón social?', 'actions':[]}
        reply=c.reply_after_proposal(proposal,people,[{'type':'save_field','field':'razon_social'}],'applicant')
        self.assertNotIn('¿Cuál es la razón social?',reply)
        self.assertIn('¿Cuál es el nombre comercial?',reply)

    def test_reused_representative_fields_ask_rfc_before_cargo(self):
        people={'rep':person('rep','representante',nombre='Nombre de prueba',correo_contacto='prueba@example.test',telefono='5551234567')}
        proposal={'reply':'Solo falta su ocupación', 'actions':[]}
        reply=c.reply_after_proposal(proposal,people,[{'type':'reuse_field'}],'rep')
        self.assertIn('¿Cuál es su RFC?',reply)
        self.assertNotIn('ocupación',reply)
        people['rep']['rfc']='ABCD010101AB1'
        self.assertIn('¿Cuál es su cargo?',c.reply_after_proposal(proposal,people,[{'type':'save_field'}],'rep'))

    def test_no_actions_preserve_model_clarification(self):
        proposal={'reply':'¿A cuál representante te refieres?', 'actions':[]}
        self.assertEqual(c.reply_after_proposal(proposal,self.people,[]),proposal['reply'])

    def test_same_contact_copies_only_common_fields_and_asks_for_missing_rfc(self):
        original = copy.deepcopy(self.people)
        proposal = {'reply':'Continuamos con el representante. ¿Cuál es su RFC?', 'actions':[
            action('add_participant','new',role='representante'),
            *[action('reuse_field','new',f,source='contact') for f in ('nombre','correo_contacto','telefono')],
        ]}
        updated, audit, active = c.apply_proposal(self.people,proposal,'El representante es el mismo contacto')
        self.assertEqual(updated[active]['answers'], original['contact']['answers'])
        self.assertEqual(c.missing(updated[active]), ['rfc','cargo'])
        self.assertEqual(self.people,original)
        self.assertEqual(audit[-1]['source_id'],'contact')

    def test_source_outside_current_intake_is_rejected_without_partial_mutation(self):
        proposal={'reply':'Continuamos', 'actions':[action('add_participant','new',role='representante'),action('reuse_field','new','nombre',source='other-intake-contact')]}
        with self.assertRaises(c.InvalidProposal):
            c.apply_proposal(self.people,proposal,'El representante es el mismo contacto')
        self.assertEqual(set(self.people),{'contact'})

    def test_existing_answers_cannot_be_overwritten_by_model(self):
        self.people['rep']=person('rep','representante',nombre='Otro nombre')
        with self.assertRaises(c.InvalidProposal):
            c.apply_proposal(self.people,{'reply':'Continúa','actions':[action('reuse_field','rep','nombre',source='contact')]},'El representante es el mismo contacto')
        self.assertEqual(self.people['rep']['answers']['nombre'],'Otro nombre')

    def test_hallucinated_value_and_role_are_rejected(self):
        with self.assertRaises(c.InvalidProposal):
            c.apply_proposal(self.people,{'reply':'Continúa','actions':[action('save_field','contact','telefono','9999999999',evidence='Mi teléfono es 5551234567')]},'Mi teléfono es 5551234567')
        with self.assertRaises(c.InvalidProposal):
            c.apply_proposal(self.people,{'reply':'Continúa','actions':[action('add_participant','new',role='representante',evidence='Hola')]},'Hola')

    def test_company_data_cannot_be_used_as_personal_contact(self):
        self.people['company']=person('company','solicitante','PM',correo_contacto='empresa@example.test')
        proposal={'reply':'Continúa','actions':[action('add_participant','new',role='representante'),action('reuse_field','new','correo_contacto',source='company')]}
        with self.assertRaises(c.InvalidProposal):
            c.apply_proposal(self.people,proposal,'El representante es el mismo contacto')

    def test_rfc_sets_aval_type_and_representative_rejects_company_rfc(self):
        message='Agregar aval ABC010101AB1'
        proposal={'reply':'¿Cuál es la razón social?', 'actions':[action('add_participant','new',role='aval',evidence=message),action('save_field','new','rfc','ABC010101AB1',evidence=message)]}
        updated,_,active=c.apply_proposal(self.people,proposal,message)
        self.assertEqual(updated[active]['subject_type'],'PM')
        self.people['rep']=person('rep','representante')
        with self.assertRaises(c.InvalidProposal):
            c.apply_proposal(self.people,{'reply':'Continúa','actions':[action('save_field','rep','rfc','ABC010101AB1',evidence=message)]},message)

    def test_context_and_resume_do_not_replay_saved_answers_or_rfc(self):
        self.people['rep']=person('rep','representante',rfc='ABCD010101AB1',nombre='Nombre de prueba')
        text=json.dumps(c.context_for(self.people,'rep'))+c.resume_reply(self.people,'rep')
        for secret in ('Nombre de prueba','prueba@example.test','5551234567','ABCD010101AB1'):
            self.assertNotIn(secret,text)
        self.assertIn('cargo',text)

    def test_equal_reuse_is_idempotent_and_role_specific_fields_rejected(self):
        self.people['rep']=person('rep','representante',nombre='Nombre de prueba')
        p={'reply':'¿Cuál es su RFC?', 'actions':[action('reuse_field','rep','nombre',source='contact')]}
        updated,_,_=c.apply_proposal(self.people,p,'El representante es el mismo contacto')
        self.assertEqual(len(updated),2)
        self.people['contact']['answers']['cargo']='Gerente'
        with self.assertRaises(c.InvalidProposal):
            c.apply_proposal(self.people,{'reply':'Continúa','actions':[action('reuse_field','rep','cargo',source='contact')]},'El representante es el mismo contacto')

    def test_malformed_action_and_unauthorized_host_are_rejected(self):
        bad=action('reuse_field','contact','nombre',source={})
        with self.assertRaises(c.InvalidProposal):
            c.apply_proposal(self.people,{'reply':'Continúa','actions':[bad]},'El representante es el mismo contacto')
        with patch.dict(os.environ,{'N8N_CONVERSATION_WEBHOOK_URL':'https://other.test/webhook/analease-conversation','N8N_CONVERSATION_WEBHOOK_TOKEN':'test'}):
            with self.assertRaises(c.ConversationUnavailable):
                c.call_agent('Hola',{},[])

    def test_duplicate_same_role_and_rfc_is_rejected(self):
        self.people['rep']=person('rep','representante',rfc='ABCD010101AB1')
        message='Agregar representante ABCD010101AB1'
        proposal={'reply':'Continúa','actions':[action('add_participant','new',role='representante',evidence=message),action('save_field','new','rfc','ABCD010101AB1',evidence=message)]}
        with self.assertRaises(c.InvalidProposal):
            c.apply_proposal(self.people,proposal,message)

    def test_allowlist_required_and_default_disabled(self):
        class Conn:
            def execute(self,*args):return self
            def fetchone(self):return {'email':'pilot@example.test'}
        with patch.dict(os.environ,{'CAPTURE_AI_ENABLED':'true','CAPTURE_AI_ALLOWED_EMAILS':''}):
            self.assertFalse(main.conversation_enabled_for(Conn(),'id'))
        with patch.dict(os.environ,{'CAPTURE_AI_ENABLED':'true','CAPTURE_AI_ALLOWED_EMAILS':'pilot@example.test'}):
            self.assertTrue(main.conversation_enabled_for(Conn(),'id'))
        with patch.dict(os.environ,{'CAPTURE_AI_ENABLED':'false','CAPTURE_AI_ALLOWED_EMAILS':'pilot@example.test'}):
            self.assertFalse(main.conversation_enabled_for(Conn(),'id'))

    def test_retried_completed_turn_does_not_call_model_or_save_again(self):
        response={'reply':'Continuamos','active_id':None,'authorization_links':[]}
        class Conn:
            def execute(self,*args):return self
            def fetchone(self):return {'user_message':'Hola','status':'complete','response_json':response}
            def transaction(self):return nullcontext()
        body=main.ConversationInput(request_id=UUID('00000000-0000-4000-8000-000000000003'),message='Hola')
        with patch.object(main,'owner',return_value='owner'), patch.object(main,'intake_for_owner',return_value={}), \
             patch.object(main,'conversation_enabled_for',return_value=True), patch.object(main.pool,'connection',return_value=nullcontext(Conn())), \
             patch.object(main,'call_agent') as agent:
            self.assertEqual(main.converse(UUID('00000000-0000-4000-8000-000000000001'),body,None),response)
            agent.assert_not_called()

if __name__=='__main__':unittest.main()
