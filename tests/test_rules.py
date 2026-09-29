import unittest
from app.rules import allowed_field, normalized_rfc, masked_name, participant_from_rfc

class RulesTest(unittest.TestCase):
    def test_masked_hint_never_returns_full_name(self):
        self.assertEqual(masked_name('Antonio Ortega Larrauri'), 'A****** O***** L*******')
        self.assertNotIn('Antonio', masked_name('Antonio Ortega Larrauri'))

    def test_rfc_subject_type(self):
        self.assertEqual(normalized_rfc(' abc010101ab1 '), ('ABC010101AB1', 'PM'))
        self.assertEqual(normalized_rfc('abcd010101ab1'), ('ABCD010101AB1', 'PF'))
        with self.assertRaises(ValueError):
            normalized_rfc('ABC123')

    def test_participant_type_is_derived_from_rfc(self):
        self.assertEqual(participant_from_rfc('aval', 'ABC010101AB1')[1], 'PM')
        self.assertEqual(participant_from_rfc('aval', 'ABCD010101AB1')[1], 'PF')
        self.assertEqual(participant_from_rfc('representante', 'ABCD010101AB1')[1], 'PF')
        with self.assertRaises(ValueError):
            participant_from_rfc('representante', 'ABC010101AB1')

    def test_fields_vary_by_role(self):
        self.assertTrue(allowed_field('aval', 'PM', 'razon_social'))
        self.assertFalse(allowed_field('aval', 'PF', 'razon_social'))
        self.assertFalse(allowed_field('representante', 'PM', 'cargo'))

if __name__ == '__main__':
    unittest.main()
