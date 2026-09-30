import json
import os
import unittest
from unittest.mock import patch
from app import mail

class FakeResponse:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, *args): return b'{"ok":true}'

class MailTest(unittest.TestCase):
    def test_sends_only_email_and_code_with_server_token(self):
        env = {'N8N_MAIL_WEBHOOK_URL':'https://example.n8n.cloud/webhook/analease-otp-send',
               'N8N_MAIL_WEBHOOK_TOKEN':'private-test-token'}
        sent = []
        def fake_open(request, timeout):
            sent.append(request)
            return FakeResponse()
        with patch.dict(os.environ, env), patch.object(mail, 'urlopen', fake_open):
            mail.send_code('cliente@example.com', '012345')
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].get_header('X-analease-token'), 'private-test-token')
        self.assertEqual(json.loads(sent[0].data), {'email':'cliente@example.com','code':'012345'})

    def test_rejects_non_https_webhook(self):
        with patch.dict(os.environ, {'N8N_MAIL_WEBHOOK_URL':'http://localhost/send',
                                     'N8N_MAIL_WEBHOOK_TOKEN':'secret'}):
            with self.assertRaises(RuntimeError):
                mail.send_code('cliente@example.com', '012345')

if __name__ == '__main__':
    unittest.main()
