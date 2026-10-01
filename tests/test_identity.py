import copy
import unittest
from app import conversation as c, memory


RFC='ABCD010101AB1'


def pf(pid,role,complete=False):
    answers={'nombre':'Persona ficticia','correo_contacto':'ficticia@example.test','telefono':'5551234567'} if complete else {}
    if complete and role=='aval':answers['ocupacion']='Ocupación del aval'
    if complete and role=='representante':answers['cargo']='Cargo del representante'
    if complete and role=='solicitante':answers.update(actividad='Actividad',pagina_web='example.test')
    rfc=RFC if role=='solicitante' or (complete and role!='contacto') else None
    return {'id':pid,'role':role,'subject_type':'PF','rfc':rfc,'answers':answers}


class IdentityTest(unittest.TestCase):
    def test_identity_reuse_works_between_every_pair_of_distinct_pf_roles(self):
        roles=('solicitante','contacto','aval','representante')
        for target_role in roles:
            for source_role in roles:
                if target_role==source_role:continue
                with self.subTest(target=target_role,source=source_role):
                    people={'source':pf('source',source_role,True),'target':pf('target',target_role)}
                    message='El '+target_role+' es el mismo '+source_role
                    proposal=c.local_reference(people,message,'target',[])
                    updated,audit,_=c.apply_proposal(people,proposal,message,'target')
                    audit.extend(c.identity_audit(updated,audit,message))
                    for field in ('nombre','correo_contacto','telefono'):
                        self.assertEqual(updated['target']['answers'][field],people['source']['answers'][field])
                    if target_role in ('aval','representante') and source_role!='contacto':
                        self.assertEqual(updated['target']['rfc'],RFC)
                    self.assertTrue(any(a['type']=='confirm_identity' for a in audit))
                    self.assertFalse(any(f in updated['target']['answers'] for f in ('cargo','ocupacion','actividad')))

    def test_new_representative_reuses_aval_rfc_and_asks_only_cargo(self):
        people={'aval':pf('aval','aval',True)}
        message='Agrega al representante; es el mismo aval'
        proposal=c.local_reference(people,message,None,[])
        self.assertEqual(proposal['actions'][0]['type'],'add_participant')
        updated,audit,active=c.apply_proposal(people,proposal,message)
        audit.extend(c.identity_audit(updated,audit,message))
        self.assertEqual(c.missing(updated[active]),['cargo'])
        self.assertEqual(updated[active]['rfc'],RFC)
        self.assertIn('¿Cuál es su cargo?',c.reply_after_proposal(proposal,updated,audit,active))
        self.assertTrue(any(a['type']=='confirm_identity' for a in audit))

    def test_new_aval_reuses_representative_but_not_cargo_as_occupation(self):
        people={'rep':pf('rep','representante',True)}
        message='Agrega un aval; es el mismo representante legal'
        proposal=c.local_reference(people,message,None,[])
        updated,audit,active=c.apply_proposal(people,proposal,message)
        self.assertEqual(c.missing(updated[active]),['ocupacion'])
        self.assertNotIn('cargo',updated[active]['answers'])
        self.assertTrue(c.identity_audit(updated,audit,message))

    def test_missing_rfc_is_requested_and_not_invented(self):
        people={'contact':pf('contact','contacto',True),'rep':pf('rep','representante')}
        message='El representante es el mismo contacto'
        proposal=c.local_reference(people,message,'rep',[])
        updated,audit,active=c.apply_proposal(people,proposal,message,'rep')
        self.assertIsNone(updated['rep']['rfc'])
        self.assertEqual(c.missing(updated['rep']),['rfc','cargo'])
        self.assertIn('¿Cuál es su RFC?',c.reply_after_proposal(proposal,updated,audit,active))

    def test_multiple_avales_require_a_number_before_adding_a_role(self):
        people={'aval1':pf('aval1','aval',True),'aval2':pf('aval2','aval',True)}
        people['aval2']['rfc']='EFGH010101AB1'
        message='Agrega al representante; es el mismo aval'
        proposal=c.local_reference(people,message,None,[])
        self.assertEqual(proposal['actions'],[])
        self.assertIn('cuál aval',proposal['reply'])
        message+=' 2'
        proposal=c.local_reference(people,message,None,[])
        updated,audit,active=c.apply_proposal(people,proposal,message)
        self.assertEqual(updated[active]['rfc'],'EFGH010101AB1')
        self.assertEqual(c.identity_audit(updated,audit,message)[0]['source_id'],'aval2')

    def test_a_pf_aval_and_pm_aval_are_still_ambiguous(self):
        people={'aval1':pf('aval1','aval',True),'aval2':pf('aval2','aval',True)}
        people['aval2']['subject_type']='PM'
        proposal=c.local_reference(people,'Agrega al representante; es el mismo aval',None,[])
        self.assertEqual(proposal['actions'],[])
        proposal=c.local_reference(people,'Agrega al representante; es el mismo aval 2',None,[])
        self.assertEqual(proposal['actions'],[])
        self.assertIn('persona moral',proposal['reply'])

    def test_conflicts_never_overwrite_or_create_partial_participants(self):
        people={'aval':pf('aval','aval',True),'rep':pf('rep','representante')}
        people['rep']['rfc']='EFGH010101AB1'
        before=copy.deepcopy(people)
        proposal=c.local_reference(people,'El representante es el mismo aval','rep',[])
        self.assertEqual(proposal['actions'],[])
        self.assertIn('conflicto',proposal['reply'])
        self.assertEqual(people,before)

    def test_same_role_same_rfc_is_not_duplicated(self):
        people={'aval':pf('aval','aval',True),'rep':pf('rep','representante',True)}
        message='Agrega al representante; es el mismo aval'
        proposal=c.local_reference(people,message,None,[])
        self.assertEqual([a['type'] for a in proposal['actions']],['focus_participant'])
        updated,audit,_=c.apply_proposal(people,proposal,message)
        self.assertEqual(len(updated),2)
        self.assertTrue(c.identity_audit(updated,audit,message))

    def test_rfc_can_anchor_identity_when_name_is_not_yet_available(self):
        people={'aval':pf('aval','aval',True),'rep':pf('rep','representante')}
        people['aval']['answers'].pop('nombre')
        message='El representante es el mismo aval'
        proposal=c.local_reference(people,message,'rep',[])
        updated,audit,_=c.apply_proposal(people,proposal,message,'rep')
        identity=c.identity_audit(updated,audit,message)
        self.assertTrue(identity)
        self.assertEqual(c.missing(updated['rep']),['nombre','cargo'])

    def test_field_only_references_do_not_confirm_identity(self):
        for message in ('El correo del representante es el mismo del aval',
                        'El RFC es el mismo del aval','el mismo del solicitante',
                        'El representante no es el mismo aval',
                        '¿El representante es el mismo aval?'):
            with self.subTest(message=message):
                self.assertIsNone(c.identity_reference(message))

    def test_confirmed_identity_is_symmetric_and_transitive_for_a_missing_field(self):
        people={'applicant':pf('applicant','solicitante',True),
                'contact':pf('contact','contacto',True),'rep':pf('rep','representante',True)}
        del people['rep']['answers']['telefono']
        del people['contact']['answers']['telefono']
        actions=[{'type':'confirm_identity','target_id':'contact','source_id':'applicant'},
                 {'type':'confirm_identity','target_id':'contact','source_id':'rep'}]
        relations=memory.relations_from_actions(people,actions)
        proposal=c.local_reference(people,'igual','rep',[],relations)
        self.assertEqual(proposal['actions'][0]['source_id'],'applicant')
        updated,_,_=c.apply_proposal(people,proposal,'igual','rep')
        self.assertEqual(updated['rep']['answers']['telefono'],'5551234567')

    def test_contact_linked_to_aval_can_supply_the_known_rfc_to_representative(self):
        people={'aval':pf('aval','aval',True),'contact':pf('contact','contacto',True),
                'rep':pf('rep','representante')}
        relations=memory.relations_from_actions(people,[{'type':'confirm_identity','target_id':'contact','source_id':'aval'}])
        message='El representante es el mismo contacto'
        proposal=c.local_reference(people,message,'rep',[],relations)
        rfc_action=next(a for a in proposal['actions'] if a['field']=='rfc')
        self.assertEqual(rfc_action['source_id'],'aval')
        updated,audit,active=c.apply_proposal(people,proposal,message,'rep')
        self.assertEqual(c.missing(updated['rep']),['cargo'])
        self.assertTrue(c.identity_audit(updated,audit,message))

    def test_a_contact_without_rfc_does_not_bridge_conflicting_rfcs(self):
        people={'aval':pf('aval','aval',True),'contact':pf('contact','contacto',True),
                'rep':pf('rep','representante',True)}
        people['rep']['rfc']='EFGH010101AB1'
        actions=[{'type':'confirm_identity','target_id':'contact','source_id':'aval'},
                 {'type':'confirm_identity','target_id':'contact','source_id':'rep'}]
        self.assertEqual(memory.relations_from_actions(people,actions),[])
        relations=memory.relations_from_actions(people,actions[:1])
        proposal=c.local_reference(people,'El representante es el mismo contacto','rep',[],relations)
        self.assertEqual(proposal['actions'],[])
        self.assertIn('conflicto',proposal['reply'])

    def test_same_role_without_known_rfc_is_not_duplicated(self):
        people={'aval':pf('aval','aval',True)}
        people['aval']['rfc']=None
        proposal=c.local_reference(people,'Agrega otro aval; es el mismo aval',None,[])
        self.assertEqual(proposal['actions'],[])
        self.assertIn('ya está registrado',proposal['reply'])

    def test_role_limit_is_enforced_before_any_new_aval_is_saved(self):
        people={'rep':pf('rep','representante',True)}
        for i in range(3):
            people['aval'+str(i)]=pf('aval'+str(i),'aval',True)
            people['aval'+str(i)]['rfc']='EFGH010101AB'+str(i)
        message='Agrega un aval; es el mismo representante'
        proposal=c.local_reference(people,message,None,[])
        original=copy.deepcopy(people)
        with self.assertRaises(c.InvalidProposal):c.apply_proposal(people,proposal,message)
        self.assertEqual(people,original)

    def test_existing_role_is_found_using_rfc_in_a_confirmed_linked_role(self):
        people={'aval':pf('aval','aval',True),'contact':pf('contact','contacto',True)}
        relations=memory.relations_from_actions(people,[{'type':'confirm_identity','target_id':'contact','source_id':'aval'}])
        message='Agrega un aval; es el mismo contacto'
        proposal=c.local_reference(people,message,None,[],relations)
        self.assertEqual([a['type'] for a in proposal['actions']],['focus_participant'])
        updated,_,_=c.apply_proposal(people,proposal,message)
        self.assertEqual(len(updated),2)


if __name__=='__main__':unittest.main()
