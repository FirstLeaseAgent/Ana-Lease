import unittest
from app.rules import allowed_field, normalized_rfc

class RulesTest(unittest.TestCase):
    def test_rfc_subject_type(self):
        self.assertEqual(normalized_rfc(' abc010101ab1 '), ('ABC010101AB1', 'PM'))
        self.assertEqual(normalized_rfc('abcd010101ab1'), ('ABCD010101AB1', 'PF'))
        with self.assertRaises(ValueError):
            normalized_rfc('ABC123')

    def test_fields_vary_by_role(self):
        self.assertTrue(allowed_field('aval', 'PM', 'razon_social'))
        self.assertFalse(allowed_field('aval', 'PF', 'razon_social'))
        self.assertFalse(allowed_field('representante', 'PM', 'cargo'))

if __name__ == '__main__':
    unittest.main()
