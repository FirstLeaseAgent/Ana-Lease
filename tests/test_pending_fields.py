import copy
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from uuid import UUID
from app import conversation as c, main, pending
from test_natural_capture import people, turn, RFC, CURP

class Conn:
    def __init__(self):self.calls=[];self.query=''
    def execute(self,q,p=()):self.calls.append((q,p));self.query=q;return self
    def fetchone(self):
        if 'SELECT * FROM capture_turns' in self.query:return None
        if 'sum(t.attempts)' in self.query:return {'n':0}
        if 'SELECT email' in self.query:return {'email':'persona@example.test'}
        raise AssertionError(self.query)
    def fetchall(self):return []
    def transaction(self):return nullcontext()

class PendingFieldTests(unittest.TestCase):
    def rows(self):
        rows=people();rows['share']={'id':'share','role':'accionista','subject_type':'PF','rfc':RFC,'company_id':'company','answers':{'nombre':'Persona Ficticia','porcentaje_participacion':'60%'}}
        return rows
    def test_common_missing_statements_advance_without_saving_a_value(self):
        for message in ['No lo tengo','no tengo ese dato','no cuento con esa información','No tengo mi CURP a la mano','no lo sé','aún no lo tengo','no recuerdo ese dato','no tengo el CURP ahorita','no lo tengo, continuemos']:
            with self.subTest(message=message):
                rows,audit,active=turn(self.rows(),message,'share')
                self.assertNotIn('curp',rows['share']['answers']);self.assertEqual(rows['share']['pending_fields'],['curp']);self.assertIsNone(active)
                self.assertEqual(audit[0]['type'],'defer_field');self.assertIn('No proporcionado',c.reply_after_proposal({'reply':'ok'},rows,audit,active))
                context=c.context_for(rows)['participants'][-1];self.assertNotIn('curp',context['known_fields']);self.assertEqual(context['pending_fields'],['curp'])
                self.assertIn('otro accionista',c.resume_reply(rows))
    def test_partial_curp_is_rejected_with_length_and_no_pending_mark(self):
        rows=self.rows();message=CURP[:-1];proposal=c.local_reference(rows,message,'share',[])
        with self.assertRaises(c.InvalidProposal) as error:c.apply_proposal(rows,proposal,message,'share')
        self.assertIn('18 caracteres',str(error.exception));self.assertIn('escribiste 17',str(error.exception));self.assertIn('no lo tengo',str(error.exception));self.assertNotIn('pending_fields',rows['share'])
        valid,_,_=turn(rows,CURP,'share');self.assertEqual(valid['share']['answers']['curp'],CURP)
    def test_pending_value_can_be_provided_later_and_is_not_reused_as_a_value(self):
        rows,_,_=turn(self.rows(),'no lo tengo','share')
        action={'type':'save_field','target_id':'share','source_id':None,'role':None,'field':'curp','value':CURP,'evidence':CURP}
        saved,audit,_=c.apply_proposal(rows,{'reply':'Gracias','actions':[action]},CURP)
        self.assertEqual(saved['share']['answers']['curp'],CURP);self.assertEqual(saved['share']['pending_fields'],[])
        rows['other']={'id':'other','role':'accionista','subject_type':'PF','rfc':'EFGH010101AB1','company_id':'company','answers':{'nombre':'Otra Persona','porcentaje_participacion':'20%'}}
        action.update(type='reuse_field',target_id='other',source_id='share',value=None,evidence='Es el mismo')
        with self.assertRaises(c.InvalidProposal):c.apply_proposal(rows,{'reply':'ok','actions':[action]},'Es el mismo','other')
    def test_unrelated_negative_data_and_questions_do_not_skip(self):
        for message in ['No tengo empleados','No quiero salir','¿Puedo dejarlo pendiente?','No tengo ese dato pero mi CURP es '+CURP,'no tengo teléfono']:
            self.assertFalse(pending.unavailable(message,'curp'),message)
    def test_forged_skip_cannot_change_another_field_or_participant(self):
        rows=self.rows();proposal=c.local_reference(rows,'no lo tengo','share',[])
        for key,value in [('target_id','company'),('field','nombre'),('value','No proporcionado')]:
            forged=copy.deepcopy(proposal);forged['actions'][0][key]=value
            with self.assertRaises(c.InvalidProposal):c.apply_proposal(rows,forged,'no lo tengo','share')
    def test_missing_rfc_requests_explicit_person_type_instead_of_guessing(self):
        rows=self.rows();rows['company']['shareholders_complete']=True;rows.pop('share')
        message='Quiero agregar un aval';proposal={'reply':'ok','actions':[{'type':'add_participant','target_id':'new','source_id':None,'role':'aval','field':None,'value':None,'evidence':message}]}
        rows,_,pid=c.apply_proposal(rows,proposal,message);rows,_,pid=turn(rows,'no tengo el RFC',pid)
        aval=next(p for p in rows.values() if p['role']=='aval');self.assertEqual(c.missing(aval),['tipo_persona']);self.assertFalse(aval['subject_type_confirmed'])
        self.assertIn('física o persona moral',c.resume_reply(rows,pid))
        rows,_,pid=turn(rows,'persona moral',pid);aval=rows[pid];self.assertEqual(aval['subject_type'],'PM');self.assertTrue(aval['subject_type_confirmed']);self.assertNotIn('rfc',c.missing(aval));self.assertIn('razon_social',c.missing(aval))
    def test_conversation_saves_pending_record_and_audit_atomically_without_model(self):
        rows=self.rows();conn=Conn();iid=UUID('00000000-0000-4000-8000-000000000001');body=main.ConversationInput(request_id=UUID('00000000-0000-4000-8000-000000000010'),message='no tengo ese dato',question=c.resume_reply(rows,'share'))
        with patch.object(main,'owner',return_value='owner'),patch.object(main,'intake_for_owner',return_value={'capture_active_id':'share','capture_version':1}),patch.object(main,'conversation_people',return_value=rows),patch.object(main,'conversation_enabled_for',return_value=True),patch.object(main,'load_relations',return_value=[]),patch.object(main.pool,'connection',return_value=nullcontext(conn)),patch.object(main,'call_agent') as agent:
            response=main.converse(iid,body,None)
        agent.assert_not_called();self.assertIn('No proporcionado',response['reply'])
        self.assertTrue(any(q.startswith('INSERT INTO capture_pending_fields') and a==(iid,'share','curp') for q,a in conn.calls));self.assertFalse(any(q.startswith('INSERT INTO answers') for q,a in conn.calls))
        self.assertTrue(any(q.startswith('UPDATE capture_turns') and "status='complete'" in q for q,a in conn.calls))
    def test_pending_fields_do_not_prevent_documents_or_completion(self):
        rows,_,_=turn(self.rows(),'no lo tengo','share');rows,_,_=turn(rows,'no hay más');rows,_,_=turn(rows,'sin aval')
        self.assertEqual(c.capture_stage(rows),'documents');progress=c.capture_progress(rows);self.assertEqual(progress['pending'],1);self.assertEqual(progress['completed'],progress['total']);self.assertEqual(progress['provided'],progress['total']-1)
    def test_non_ai_answer_marks_pending_and_does_not_store_invalid_value(self):
        rows=self.rows();conn=Conn();iid=UUID('00000000-0000-4000-8000-000000000001');pid=UUID('00000000-0000-4000-8000-000000000011');rows[str(pid)]=rows.pop('share')
        body=main.AnswerInput(field_code='curp',value='no tengo ese dato')
        with patch.object(main,'owner',return_value='owner'),patch.object(main,'intake_for_owner'),patch.object(main,'conversation_people',return_value=rows),patch.object(main.pool,'connection',return_value=nullcontext(conn)):
            self.assertTrue(main.save_answer(iid,pid,body,None)['pending'])
        self.assertFalse(any(q.startswith('INSERT INTO answers') for q,a in conn.calls))
