import copy
import unittest
import json
from pathlib import Path
from app import conversation as c, memory, shareholders
from test_natural_capture import people, relations, RFC, CURP

def action(kind,message,target='new',role=None,field=None,value=None,source=None):
    return {'type':kind,'target_id':target,'role':role,'field':field,'value':value,'source_id':source,'evidence':message}

class AgentTransitionTests(unittest.TestCase):
    def test_recorded_actual_n8n_proposals_validate_and_server_does_not_repeat_saved_fields(self):
        fixtures=json.loads(Path(__file__).with_name('fixtures').joinpath('n8n-natural-cases.json').read_text())
        for case in fixtures['cases']:
            with self.subTest(case=case['case']):
                rows=people();active=None
                if case['case']==1:
                    rows['share']={'id':'share','role':'accionista','subject_type':'PF','rfc':RFC,'company_id':'company','answers':{'nombre':'Nombre Ficticio'}}
                    active='share'
                updated,audit,pid=c.apply_proposal(rows,case['proposal'],case['message'],active)
                reply=c.reply_after_proposal(case['proposal'],updated,audit,pid)
                if case['case']==0:
                    self.assertEqual(updated[pid]['rfc'],RFC)
                    self.assertEqual(updated[pid]['answers']['porcentaje_participacion'],'99.6%')
                    self.assertTrue(c.identity_audit(updated,audit,case['message']))
                    self.assertIn('CURP',reply)
                if case['case']==1:
                    self.assertEqual(c.missing(updated['share']),[])
                    self.assertNotIn('CURP',reply)
                if case['case']==2:self.assertEqual(c.capture_stage(updated),'guarantors')

    def test_context_exposes_stage_role_company_and_new_role_catalog_without_personal_values(self):
        rows=people();context,_=c.context_for_turn(rows,None,[],relations=relations(rows))
        self.assertEqual(context['capture_stage'],'shareholders')
        transition=context['participant_transition']
        self.assertEqual(transition['pending_role'],'accionista')
        self.assertEqual(transition['company_id'],'company')
        self.assertTrue(transition['can_finish'])
        self.assertIn('curp',[r['code'] for r in transition['new_participant_schema']['PF']])
        self.assertNotIn('curp',[r['code'] for r in transition['new_participant_schema']['PM']])

    def test_unrecognized_natural_stage_answer_reaches_agent_and_creation_is_grounded(self):
        rows=people();message='El socio mayoritario es el mismo contacto; su participación es 99.6%'
        self.assertIsNone(c.local_reference(rows,message,None,[],relations(rows)))
        proposal={'reply':'Seguimos','actions':[
            action('add_participant',message,role='accionista'),
            action('reuse_field',message,field='rfc',source='rep'),
            action('reuse_field',message,field='nombre',source='contact'),
            action('save_field',message,field='porcentaje_participacion',value='99.6%')]}
        updated,audit,pid=c.apply_proposal(rows,proposal,message)
        self.assertEqual(updated[pid]['rfc'],RFC)
        self.assertEqual(c.missing(updated[pid]),['curp'])
        self.assertTrue(c.identity_audit(updated,audit,message))
        self.assertIn('CURP',c.reply_after_proposal(proposal,updated,audit,pid))

    def test_model_can_extract_curp_and_percentage_together_from_current_message(self):
        rows=people();rows['share']={'id':'share','role':'accionista','subject_type':'PF','rfc':RFC,
            'company_id':'company','answers':{'nombre':'Nombre Ficticio'}}
        message='Su CURP es '+CURP+' y tiene el 60%'
        self.assertIsNone(c.local_reference(rows,message,'share',[]))
        proposal={'reply':'Seguimos','actions':[action('save_field',message,'share',field='curp',value=CURP),
            action('save_field',message,'share',field='porcentaje_participacion',value='60%')]}
        updated,_,pid=c.apply_proposal(rows,proposal,message,'share')
        self.assertEqual(updated['share']['answers']['porcentaje_participacion'],'60%')
        self.assertIsNone(pid)

    def test_natural_model_closure_passes_but_cannot_close_wrong_stage_or_pending_record(self):
        for message in ['Con él terminamos','Ya terminé de registrar a los socios','No tengo más accionistas']:
            rows=people();self.assertIsNone(c.local_reference(rows,message,None,[]))
            updated,_,_=c.apply_proposal(rows,{'reply':'Seguimos','actions':[action('finish_shareholders',message,'company')]},message)
            self.assertEqual(c.capture_stage(updated),'guarantors')
        for message in ['ok','¿Ya terminé?','Ya terminé de registrar a los avales']:
            with self.assertRaises(c.InvalidProposal):c.apply_proposal(people(),{'reply':'Seguimos','actions':[action('finish_shareholders',message,'company')]},message)
        rows=people();rows['share']={'id':'share','role':'accionista','subject_type':'PF','rfc':RFC,'company_id':'company','answers':{}}
        message='Con él terminamos'
        with self.assertRaises(c.InvalidProposal):c.apply_proposal(rows,{'reply':'Seguimos','actions':[action('finish_shareholders',message,'company')]},message)

    def test_unknown_fields_and_values_not_in_message_remain_rejected_atomically(self):
        rows=people();message='El socio mayoritario es el mismo contacto'
        original=copy.deepcopy(rows)
        for field,value in [('campo_inventado','contacto'),('nombre','Nombre inventado')]:
            proposal={'reply':'Seguimos','actions':[action('add_participant',message,role='accionista'),action('save_field',message,field=field,value=value)]}
            with self.assertRaises(c.InvalidProposal):c.apply_proposal(rows,proposal,message)
            self.assertEqual(rows,original)

    def test_creation_in_wrong_stage_and_acknowledgements_are_rejected(self):
        for message in ['ok','hola','gracias','¿Quién será?']:
            with self.assertRaises(c.InvalidProposal):c.apply_proposal(people(),{'reply':'Seguimos','actions':[action('add_participant',message,role='accionista')]},message)
        message='El socio mayoritario es el mismo contacto'
        rows=people();rows['rep']['answers'].pop('cargo')
        with self.assertRaises(c.InvalidProposal):c.apply_proposal(rows,{'reply':'Seguimos','actions':[action('add_participant',message,role='accionista')]},message,'rep')

    def test_shared_channels_and_negative_identity_do_not_create_identity_memory(self):
        rows=people();rows['share']={'id':'share','role':'accionista','subject_type':'PF','rfc':RFC,'company_id':'company','answers':{'nombre':rows['contact']['answers']['nombre']}}
        audit=[{'type':'reuse_field','target_id':'share','source_id':'contact','field':'nombre'}]
        for message in ['El correo es el mismo contacto','no es el mismo contacto','¿es el mismo contacto?']:
            self.assertEqual(c.identity_audit(rows,audit,message),[])
