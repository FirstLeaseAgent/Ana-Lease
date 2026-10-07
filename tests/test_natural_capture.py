import copy
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from uuid import UUID
from app import conversation as c, memory, main

RFC='ABCD010101AB1'
CURP='AACD010101HDFRRL01'

def people():
    return {
        'company':{'id':'company','role':'solicitante','subject_type':'PM','rfc':'ABC010101AB1',
            'answers':{f:'Dato ficticio' for f in c.FIELDS['solicitante']['PM']},
            'shareholders_enabled':True,'shareholders_complete':False,'guarantors_complete':False},
        'contact':{'id':'contact','role':'contacto','subject_type':'PF','rfc':None,
            'answers':{'nombre':'Nombre Ficticio Rincón','correo_contacto':'ficticio@example.test','telefono':'5551234567'}},
        'rep':{'id':'rep','role':'representante','subject_type':'PF','rfc':RFC,
            'answers':{'nombre':'Nombre Ficticio Rincón','correo_contacto':'ficticio@example.test','telefono':'5551234567','cargo':'Director'}},
    }

def relations(rows):
    return memory.relations_from_actions(rows,[{'type':'confirm_identity','target_id':'rep','source_id':'contact'}])

def turn(rows,message,preferred=None,links=None):
    proposal=c.local_reference(rows,message,preferred,[],links)
    if proposal is None:raise AssertionError('Unexpected model call: '+message)
    updated,audit,active=c.apply_proposal(rows,proposal,message,preferred)
    audit.extend(c.identity_audit(updated,audit,message))
    return updated,audit,active

class NaturalCaptureTests(unittest.TestCase):
    def test_full_transcript_saves_percentage_before_curp_closes_and_accepts_occupation(self):
        rows=people();links=relations(rows)
        message='el principal con 99.6% de participación es el mismo que el contacto'
        rows,audit,pid=turn(rows,message,links=links)
        shareholder=pid
        self.assertEqual(rows[pid]['rfc'],RFC)
        self.assertEqual(rows[pid]['answers'],{'nombre':'Nombre Ficticio Rincón','porcentaje_participacion':'99.6%'})
        self.assertTrue(any(a['type']=='confirm_identity' and a['source_id']=='contact' for a in audit))
        self.assertEqual(c.missing(rows[pid]),['curp'])
        links+=memory.relations_from_actions(rows,audit)
        rows,_,pid=turn(rows,CURP,pid,links)
        self.assertIsNone(pid)
        self.assertIn('otro accionista',c.resume_reply(rows))
        rows,_,_=turn(rows,'no hay mas',links=links)
        self.assertEqual(c.capture_stage(rows),'guarantors')
        rows,audit,pid=turn(rows,'es el mismo que el representante',links=links)
        self.assertEqual(rows[pid]['role'],'aval')
        self.assertEqual(rows[pid]['rfc'],RFC)
        self.assertNotIn('ocupacion',rows[pid]['answers'])
        self.assertNotIn('porcentaje_participacion',rows[pid]['answers'])
        rows,_,pid=turn(rows,'Director',pid,links)
        aval=next(p for p in rows.values() if p['role']=='aval')
        self.assertEqual(aval['answers']['ocupacion'],'Director')
        self.assertIsNone(pid)
        rows,_,_=turn(rows,'son todos',links=links)
        self.assertEqual(c.capture_stage(rows),'documents')
        self.assertEqual(rows[shareholder]['answers']['porcentaje_participacion'],'99.6%')

    def test_combined_answer_word_order_comma_and_literal_whitespace(self):
        for message in ['es el mismo contacto y tiene el 99.6%',
                        'El accionista es el mismo representante legal, con 99,6% de participación',
                        'EL PRINCIPAL CON 99.6  % DE PARTICIPACIÓN ES EL MISMO QUE EL CONTACTO']:
            with self.subTest(message=message):
                rows=people();updated,_,pid=turn(rows,message,links=relations(rows))
                self.assertEqual(updated[pid]['rfc'],RFC)
                self.assertIn('porcentaje_participacion',updated[pid]['answers'])

    def test_combined_answer_ambiguous_source_saves_nothing(self):
        rows=people();rows['other']=copy.deepcopy(rows['contact']);rows['other']['id']='other'
        original=copy.deepcopy(rows)
        proposal=c.local_reference(rows,'el principal con 60% es el mismo contacto',None,[])
        self.assertEqual(proposal['actions'],[])
        self.assertEqual(rows,original)

    def test_invalid_percentage_or_total_rejects_whole_combined_turn(self):
        for percentage in ['10%','101%']:
            rows=people();original=copy.deepcopy(rows)
            with self.assertRaises(c.InvalidProposal):turn(rows,'es el mismo contacto y tiene el '+percentage,links=relations(rows))
            self.assertEqual(rows,original)
        rows=people()
        rows['existing']={'id':'existing','role':'accionista','subject_type':'PF','rfc':'EFGH010101AB1',
            'company_id':'company','answers':{'nombre':'Otra Persona','curp':CURP,'porcentaje_participacion':'60%'}}
        original=copy.deepcopy(rows)
        with self.assertRaises(c.InvalidProposal):turn(rows,'es el mismo contacto y tiene el 50%',links=relations(rows))
        self.assertEqual(rows,original)

    def test_questions_negations_and_shared_phone_do_not_imply_shareholder_identity(self):
        for message in ['¿es el mismo contacto y tiene el 60%?',
                        'no es el mismo contacto y tiene el 60%',
                        'el teléfono es el mismo contacto y tiene el 60%']:
            proposal=c.local_reference(people(),message,None,[])
            self.assertFalse(proposal and proposal['actions'])

    def test_list_close_cannot_skip_pending_curp_or_occupation(self):
        rows=people();rows,_,pid=turn(rows,'es el mismo contacto y tiene el 60%',links=relations(rows))
        updated,audit,active=turn(rows,'no hay más',pid)
        self.assertEqual(updated,rows)
        self.assertEqual(audit,[])
        self.assertEqual(active,pid)
        self.assertFalse(updated['company']['shareholders_complete'])
        aligned,alignment=c.align_direct_answer(c.local_reference(rows,'son todos',pid,[]),rows,'son todos',pid,c.resume_reply(rows,pid))
        self.assertEqual(aligned['actions'],[])
        self.assertIsNone(alignment)

    def test_natural_closures_are_stage_specific_and_ok_still_does_not_close(self):
        for message in ['no hay más','no hay mas','son todos','eso es todo','ninguno']:
            updated,_,_=turn(people(),message)
            self.assertTrue(updated['company']['shareholders_complete'])
            self.assertFalse(updated['company']['guarantors_complete'])
        updated,audit,_=turn(people(),'ok')
        self.assertFalse(updated['company']['shareholders_complete'])
        self.assertEqual(audit,[])

    def test_name_and_rfc_can_start_the_person_requested_by_the_current_stage(self):
        for message in ['Nombre Ficticio Rincón','Otra Persona Ficticia',RFC,'XYZ010101AB1']:
            rows=people();updated,audit,pid=turn(rows,message,links=relations(rows))
            self.assertEqual(updated[pid]['role'],'accionista')
            if message=='Nombre Ficticio Rincón':
                self.assertEqual(updated[pid]['rfc'],RFC)
                self.assertTrue(any(a['type']=='confirm_identity' for a in audit))
            if message=='XYZ010101AB1':self.assertEqual(updated[pid]['subject_type'],'PM')
        rows=people();rows['company']['shareholders_complete']=True
        updated,_,pid=turn(rows,'Otra Persona Ficticia')
        self.assertEqual(updated[pid]['role'],'aval')

    def test_combined_identity_does_not_create_a_shareholder_while_cargo_is_pending(self):
        rows=people();rows['rep']['answers'].pop('cargo')
        message='el principal con 60% es el mismo contacto'
        proposal={'reply':'Seguimos','actions':[{'type':'add_participant','target_id':'new','source_id':None,'role':'accionista','field':None,'value':None,'evidence':message}]}
        with self.assertRaises(c.InvalidProposal):c.apply_proposal(rows,proposal,message,'rep')

    def test_occupation_repeat_fallback_and_questions_remain_clarifications(self):
        rows={'aval':{'id':'aval','role':'aval','subject_type':'PF','rfc':RFC,'answers':{'nombre':'Nombre Ficticio'}}}
        proposal={'reply':'Gracias. ¿Cuál es su ocupación?','actions':[]}
        aligned,_=c.align_direct_answer(proposal,rows,'Director','aval',c.resume_reply(rows))
        updated,_,_=c.apply_proposal(rows,aligned,'Director','aval')
        self.assertEqual(updated['aval']['answers']['ocupacion'],'Director')
        for message in ['no sé','¿Director?','soy yo','el mismo contacto','ok','gracias']:
            proposal=c.local_reference(rows,message,'aval',[])
            self.assertFalse(proposal and any(a['type']=='save_field' for a in proposal['actions']))

    def test_converse_atomically_persists_combined_identity_and_percentage(self):
        rows=people()
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
        body=main.ConversationInput(request_id=UUID('00000000-0000-4000-8000-000000000002'),message='el principal con 99.6% de participación es el mismo que el contacto')
        with patch.object(main,'owner',return_value='owner'),patch.object(main,'intake_for_owner',return_value={'capture_active_id':None,'capture_version':1}),patch.object(main,'conversation_people',return_value=rows),patch.object(main,'conversation_enabled_for',return_value=True),patch.object(main,'load_relations',return_value=relations(rows)),patch.object(main.pool,'connection',return_value=nullcontext(conn)),patch.object(main,'call_agent') as agent:
            response=main.converse(iid,body,None)
        agent.assert_not_called()
        self.assertIn('CURP',response['reply'])
        self.assertNotIn('Nombre Ficticio',response['reply'])
        saved=[p for q,p in conn.calls if q.startswith('INSERT INTO answers')]
        self.assertTrue(any(p[2]=='porcentaje_participacion' for p in saved))
        committed=[p for q,p in conn.calls if q.startswith('UPDATE capture_turns') and "status='complete'" in q]
        self.assertEqual(len(committed),1)
        self.assertTrue(any(a['type']=='confirm_identity' for a in committed[0][1].obj))
