from pathlib import Path
import tempfile
import unittest

from phone_gateway import PhoneAccess, make_gateway, permitted

PHONE, PC = '100.101.1.2', '100.101.1.1'


class PhoneGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.access = PhoneAccess(Path(self.temp.name) / 'phone.json', clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def test_code_login_and_session_expiry(self):
        code = self.access.new_code()
        token = self.access.login(code)
        self.assertTrue(self.access.valid(token))
        self.assertFalse(self.access.valid('other'))
        self.now += 31 * 86400
        self.assertFalse(self.access.valid(token))

    def test_new_code_ends_sessions_and_code_is_not_stored_plain(self):
        code = self.access.new_code()
        token = self.access.login(code)
        self.assertNotIn(code, (Path(self.temp.name) / 'phone.json').read_text())
        self.access.new_code()
        self.assertFalse(self.access.valid(token))

    def test_five_wrong_codes_lock_for_fifteen_minutes(self):
        code = self.access.new_code()
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(5):
            self.assertIsNone(self.access.login(wrong))
        self.assertIsNone(self.access.login(code))
        self.now += 901
        self.assertIsNotNone(self.access.login(code))

    def test_only_pinned_phone_and_simple_screen_routes(self):
        self.assertTrue(permitted('GET', '/', PHONE, PHONE))
        self.assertTrue(permitted('POST', '/api/approve-execute/confirm', PHONE, PHONE))
        self.assertFalse(permitted('GET', '/', '100.101.1.9', PHONE))
        self.assertFalse(permitted('GET', '/advanced', PHONE, PHONE))
        self.assertFalse(permitted('POST', '/api/execute', PHONE, PHONE))
        self.assertFalse(permitted('POST', '/api/connections/meta-executor/setup', PHONE, PHONE))

    def test_refuses_non_tailscale_addresses(self):
        for bind, phone in (('0.0.0.0', PHONE), ('192.168.1.5', PHONE), (PC, '8.8.8.8'), (PC, PC)):
            with self.assertRaises(ValueError):
                make_gateway(bind, phone, self.access, port=0)


if __name__ == '__main__':
    unittest.main()
