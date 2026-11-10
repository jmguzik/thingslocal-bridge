import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fixtures import configuration
from thingslocal import cloud


class IdentityTests(unittest.TestCase):
    def test_qr_has_no_fixed_model_manufacturer_or_setup_id(self):
        identity = cloud.qr_identity('https://qr.samsungiots.com/?m=0AwG&s=123&r=OTHER123456')
        self.assertEqual(identity, {'manufacturer': '0AwG', 'setup_id': '123', 'serial': 'OTHER123456'})
        for url in ('http://qr.samsungiots.com/?m=0AwG&s=123&r=OTHER123456',
                    'https://qr.samsungiots.com.evil.test/?m=0AwG&s=123&r=OTHER123456',
                    'https://qr.samsungiots.com/?m=0AwG&s=123&s=000&r=OTHER123456'):
            with self.assertRaises(ValueError): cloud.qr_identity(url)

    def test_different_ap_product_prefixes_are_verified_against_selected_identity(self):
        identity = configuration()['devices'][1]['identity']
        hybrid = cloud.serial_hash(identity['serial'])[:4] + identity['serial'][-4:]
        for prefix in ('Dishwasher', 'Cooktop', 'Washer_Dryer', 'Another appliance'):
            for tail in ('_E40AwG0006' + hybrid, '_E50AwG000' + hybrid):
                self.assertTrue(cloud.matching_ap(prefix + tail, identity))
        self.assertFalse(cloud.matching_ap('Dishwasher_E40AwG0016' + hybrid, identity))
        self.assertFalse(cloud.matching_ap('Dishwasher_E40AwG0006ABCDEFGH', identity))

    def test_fresh_key_request_saves_only_full_hash_verified_material(self):
        device = configuration()['devices'][1]
        def response(url, token, payload):
            return 200, {'data': [{'type': 'ECDH_ED25519', 'sn': cloud.serial_hash(device['identity']['serial']),
                                  'cloudPubKey': base64.urlsafe_b64encode(b'p' * 32).decode(),
                                  'blob': base64.urlsafe_b64encode(b'b' * 32).decode()}]}
        with tempfile.TemporaryDirectory() as directory, patch('thingslocal.cloud.request', side_effect=response):
            result = cloud.acquire_material(directory, device, 'fixture-token')
            self.assertTrue(result['identity_hash_verified'])
            saved = Path(directory) / 'setup-material/washer.json'
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('fixture-token', saved.read_text())
            self.assertEqual(json.loads(saved.read_text())['identity'], device['identity'])
        with tempfile.TemporaryDirectory() as directory, patch('thingslocal.cloud.request', return_value=(200, {'data': []})):
            with self.assertRaises(ValueError): cloud.acquire_material(directory, device, 'fixture-token')
            self.assertFalse((Path(directory) / 'setup-material/washer.json').exists())
