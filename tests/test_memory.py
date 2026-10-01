import copy
import json
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from uuid import UUID

from app import conversation as c, main, memory


def person(pid,role,**answers):
    return {'id':pid,'role':role,'subject_type':'PF','rfc':None,'answers':answers}


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.people={
            'contact':person('contact','contacto',nombre='Persona ficticia',
                             correo_contacto='ficticia@example.test',telefono='5551234567'),
            'rep':person('rep','representante'),
        }

    def identity_turn(self):
        message='Los datos del representante son los mismos que el contacto'
        proposal=c.local_reference(self.people,message,'rep',[])
        people,audit,_=c.apply_proposal(self.people,proposal,message,'rep')
        audit.extend(c.identity_audit(people,audit,message))
        return people,audit

    def test_identity_and_reuse_survive_serialization_without_personal_values(self):
        people,audit=self.identity_turn()
        persisted=json.loads(json.dumps(audit))
        relations=memory.relations_from_actions(people,persisted)
        self.assertEqual(len(relations),4)
        self.assertEqual(relations[-1],{'type':'same_person','target_id':'rep',
                                      'source_id':'contact','source_role':'contacto'})
        payload=json.dumps(memory.model_memory(relations,[]))
        for value in self.people['contact']['answers'].values():
            self.assertNotIn(value,payload)

    def test_a_shared_email_does_not_confirm_identity(self):
        people=copy.deepcopy(self.people)
        people['rep']['answers']['correo_contacto']=people['contact']['answers']['correo_contacto']
        audit=[{'type':'reuse_field','target_id':'rep','source_id':'contact','field':'correo_contacto'}]
        self.assertEqual(c.identity_audit(people,audit,'el mismo del contacto'),[])
        relations=memory.relations_from_actions(people,audit)
        self.assertEqual([r['type'] for r in relations],['shared_field'])
        self.assertIsNone(c.local_reference(people,'igual','rep',[],relations))

    def test_old_identity_remains_available_outside_recent_history(self):
        people,audit=self.identity_turn()
        # A common field is pending again; the older confirmed identity resolves it.
        del people['rep']['answers']['telefono']
        relations=memory.relations_from_actions(people,audit)
        history=[{'user':'Mensaje posterior','assistant':'Seguimos','capture_targets':[]} for _ in range(12)]
        people['rep']['rfc']='ABCD010101AB1'
        people['rep']['answers']['cargo']='Director General'
        context,recent=c.context_for_turn(people,'rep',history,c.resume_reply(people,'rep'),relations=relations)
        self.assertEqual(context['current_question']['field'],'telefono')
        self.assertEqual(len(context['conversation_memory']['earlier_turns']),9)
        self.assertLessEqual(len(recent),4)
        self.assertTrue(any(r['type']=='same_person' for r in context['conversation_memory']['confirmed_relations']))
        proposal=c.local_reference(people,'igual','rep',recent,relations)
        updated,audit,active=c.apply_proposal(people,proposal,'igual','rep')
        self.assertEqual(updated['rep']['answers']['telefono'],people['contact']['answers']['telefono'])
        self.assertEqual(c.missing(updated['rep']),[])

    def test_wrong_intake_and_changed_values_invalidate_memory(self):
        people,audit=self.identity_turn()
        self.assertEqual(memory.relations_from_actions({'rep':people['rep']},audit),[])
        people['rep']['answers']['nombre']='Otra persona'
        relations=memory.relations_from_actions(people,audit)
        self.assertFalse(any(r['type']=='same_person' for r in relations))
        self.assertFalse(any(r.get('field')=='nombre' for r in relations))
        people['rep']['answers'].pop('telefono')
        self.assertFalse(any(r.get('field')=='telefono' for r in memory.relations_from_actions(people,audit)))

    def test_rfc_conflicts_and_company_identity_are_rejected(self):
        people,audit=self.identity_turn()
        people['rep']['rfc']='ABCD010101AB1'
        people['contact']['rfc']='EFGH010101AB1'
        self.assertFalse(any(r['type']=='same_person' for r in memory.relations_from_actions(people,audit)))
        people['contact']['rfc']=None
        people['contact']['subject_type']='PM'
        self.assertFalse(any(r['type']=='same_person' for r in memory.relations_from_actions(people,audit)))

    def test_query_scopes_to_intake_and_uses_latest_committed_action_per_field(self):
        class Conn:
            def execute(self,query,params):self.query=query;self.params=params;return self
            def fetchall(self):return [{'action':{'type':'save_field','target_id':'rep','field':'nombre'}}]
        conn=Conn()
        self.assertEqual(memory.load_relations(conn,'intake-only',self.people),[])
        self.assertEqual(conn.params,('intake-only',))
        self.assertIn("t.status='complete'",conn.query)
        self.assertIn('DISTINCT ON',conn.query)
        self.assertIn('a.action_order DESC',conn.query)

    def test_no_capture_action_cannot_advance_to_another_field(self):
        people=copy.deepcopy(self.people)
        people['rep']['rfc']='ABCD010101AB1'
        people['rep']['answers']['nombre']='Persona ficticia'
        proposal={'reply':'Gracias. ¿Cuál es el teléfono?','actions':[]}
        self.assertIn('¿Cuál es su cargo?',c.reply_after_proposal(proposal,people,[],'rep'))

    def test_route_commits_identity_with_answers_and_actual_shown_reply(self):
        people=copy.deepcopy(self.people)
        people['rep']['rfc']='ABCD010101AB1'
        message='Los datos del representante son los mismos que el contacto'
        intake_id=UUID('00000000-0000-4000-8000-000000000001')
        class Conn:
            def __init__(self):self.query='';self.calls=[]
            def execute(self,query,params=()):self.query=query;self.calls.append((query,params));return self
            def fetchone(self):
                if 'SELECT * FROM capture_turns' in self.query:return None
                if 'sum(t.attempts)' in self.query:return {'n':0}
                if 'SELECT email FROM users' in self.query:return {'email':'ficticia@example.test'}
                raise AssertionError(self.query)
            def fetchall(self):return []
            def transaction(self):return nullcontext()
        conn=Conn()
        body=main.ConversationInput(request_id=UUID('00000000-0000-4000-8000-000000000002'),
                                    message=message,question=c.resume_reply(people,'rep'))
        with patch.object(main,'owner',return_value='owner'), \
             patch.object(main,'intake_for_owner',return_value={'capture_active_id':'rep','capture_version':1}), \
             patch.object(main,'conversation_enabled_for',return_value=True), \
             patch.object(main,'conversation_people',return_value=people), \
             patch.object(main.pool,'connection',return_value=nullcontext(conn)), \
             patch.object(main,'call_agent') as agent:
            response=main.converse(intake_id,body,None)
        agent.assert_not_called()
        writes=[params for query,params in conn.calls if query.startswith('INSERT INTO answers')]
        self.assertEqual([params[2] for params in writes],['nombre','correo_contacto','telefono'])
        commit=next(params for query,params in conn.calls if query.startswith('UPDATE capture_turns SET status=\'complete\''))
        self.assertEqual(commit[0].obj,response)
        self.assertEqual(commit[1].obj[-1]['type'],'confirm_identity')
        self.assertIn('¿Cuál es su cargo?',response['reply'])

    def test_route_delivers_durable_memory_and_recent_history_to_agent(self):
        people,audit=self.identity_turn()
        people['rep']['rfc']='ABCD010101AB1'
        people['company']={'id':'company','role':'solicitante','subject_type':'PM','rfc':'ABC010101AB1',
                           'answers':{field:'Dato ficticio' for field in c.FIELDS['solicitante']['PM']}}
        class Conn:
            def __init__(self):self.query='';self.calls=[]
            def execute(self,query,params=()):self.query=query;self.calls.append((query,params));return self
            def fetchone(self):
                if 'SELECT * FROM capture_turns' in self.query:return None
                if 'sum(t.attempts)' in self.query:return {'n':0}
                if 'SELECT email FROM users' in self.query:return {'email':'ficticia@example.test'}
                raise AssertionError(self.query)
            def fetchall(self):
                if 'DISTINCT ON' in self.query:return [{'action':a} for a in audit]
                return [{'user_message':'Mensaje posterior '+str(i),'response_json':{'reply':'Seguimos'},
                         'audit_json':[]} for i in range(12)]
            def transaction(self):return nullcontext()
        conn=Conn()
        iid=UUID('00000000-0000-4000-8000-000000000001')
        body=main.ConversationInput(request_id=UUID('00000000-0000-4000-8000-000000000002'),
                                   message='Director General',question=c.resume_reply(people,'rep'))
        proposal={'reply':'Continuamos','actions':[{'type':'save_field','target_id':'rep','source_id':None,
                  'role':None,'field':'cargo','value':'Director General','evidence':'Director General'}]}
        with patch.object(main,'owner',return_value='owner'), \
             patch.object(main,'intake_for_owner',return_value={'capture_active_id':'rep','capture_version':1}), \
             patch.object(main,'conversation_enabled_for',return_value=True), \
             patch.object(main,'conversation_people',return_value=people), \
             patch.object(main.pool,'connection',return_value=nullcontext(conn)), \
             patch.object(main,'call_agent',return_value=proposal) as agent:
            response=main.converse(iid,body,None)
        _,context,history=agent.call_args.args
        self.assertLessEqual(len(history),4)
        self.assertEqual(len(context['conversation_memory']['earlier_turns']),9)
        self.assertTrue(any(r['type']=='same_person' for r in context['conversation_memory']['confirmed_relations']))
        self.assertEqual(context['current_question']['field'],'cargo')
        self.assertIn('documentos',response['reply'])

    def test_concurrent_change_does_not_commit_answers_or_memory(self):
        people=copy.deepcopy(self.people)
        people['rep']['rfc']='ABCD010101AB1'
        message='Los datos del representante son los mismos que el contacto'
        class Conn:
            def __init__(self):self.query='';self.calls=[]
            def execute(self,query,params=()):self.query=query;self.calls.append((query,params));return self
            def fetchone(self):
                if 'SELECT * FROM capture_turns' in self.query:return None
                if 'sum(t.attempts)' in self.query:return {'n':0}
                if 'SELECT email FROM users' in self.query:return {'email':'ficticia@example.test'}
                raise AssertionError(self.query)
            def fetchall(self):return []
            def transaction(self):return nullcontext()
        conn=Conn()
        body=main.ConversationInput(request_id=UUID('00000000-0000-4000-8000-000000000002'),
                                   message=message,question=c.resume_reply(people,'rep'))
        with patch.object(main,'owner',return_value='owner'), \
             patch.object(main,'intake_for_owner',side_effect=[{'capture_active_id':'rep','capture_version':1},
                                                             {'capture_active_id':'rep','capture_version':2}]), \
             patch.object(main,'conversation_enabled_for',return_value=True), \
             patch.object(main,'conversation_people',return_value=people), \
             patch.object(main.pool,'connection',return_value=nullcontext(conn)):
            with self.assertRaises(main.HTTPException) as error:
                main.converse(UUID('00000000-0000-4000-8000-000000000001'),body,None)
        self.assertEqual(error.exception.status_code,409)
        self.assertFalse(any(query.startswith('INSERT INTO answers') for query,_ in conn.calls))
        self.assertFalse(any("SET status='complete'" in query for query,_ in conn.calls))


if __name__=='__main__':unittest.main()
