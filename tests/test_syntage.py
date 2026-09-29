import json
import os
import unittest
from unittest.mock import patch

from app import syntage


class FakeResponse:
    status = 200
    def read(self, size):
        return json.dumps({
            'ok': True, 'person_type': 'legal', 'registered': True,
            'sat': 'valid', 'buro': 'missing', 'next_action': 'onboarding',
            'entity_id': 'must-not-escape',
        }).encode()


class FakeConnection:
    request_args = None
    def __init__(self, host, timeout):
        self.host = host
        self.timeout = timeout
    def request(self, method, path, body, headers):
        self.request_args = (method, path, json.loads(body), headers)
    def getresponse(self):
        return FakeResponse()
    def close(self):
        pass


class SyntageTest(unittest.TestCase):
    def test_sends_only_rfc_and_returns_allowed_status(self):
        env = {
            'N8N_SYNTAGE_WEBHOOK_URL': 'https://flagent.app.n8n.cloud/webhook/analease-syntage-status',
            'N8N_SYNTAGE_WEBHOOK_TOKEN': 'test-secret',
        }
        with patch.dict(os.environ, env), patch.object(syntage.http.client, 'HTTPSConnection', FakeConnection):
            data = syntage.check_status('FLE180212MC1')
        self.assertEqual(data['next_action'], 'onboarding')
        self.assertNotIn('entity_id', data)

    def test_rejects_redirect_or_wrong_host_configuration(self):
        with patch.dict(os.environ, {
            'N8N_SYNTAGE_WEBHOOK_URL': 'https://other.example/webhook/analease-syntage-status',
            'N8N_SYNTAGE_WEBHOOK_TOKEN': 'test-secret',
        }):
            with self.assertRaises(syntage.SyntageUnavailable):
                syntage.check_status('FLE180212MC1')


if __name__ == '__main__':
    unittest.main()
