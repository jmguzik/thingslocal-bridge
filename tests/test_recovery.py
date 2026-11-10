from pathlib import Path
import tempfile
import unittest
from fixtures import configuration,registration
from thingslocal.recovery import export_legacy_registration
from thingslocal.settings import atomic_json


class RecoveryTests(unittest.TestCase):
    def test_rollback_preserves_new_device_id_and_excludes_the_other_appliance(self):
        config=configuration();records=[registration(item) for item in config['devices']]
        with tempfile.TemporaryDirectory() as directory:
            atomic_json(Path(directory)/'registrations.json',records)
            exported=export_legacy_registration(config,directory,'hob','legacy-hob')
            self.assertEqual(len(exported),1);self.assertEqual(exported[0]['id'],records[0]['id'])
            self.assertEqual(exported[0]['name'],'legacy-hob')
            self.assertEqual(exported[0]['serialNumber'],config['devices'][0]['identity']['serial'])

    def test_absent_new_registration_is_not_fabricated_from_old_data(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):export_legacy_registration(configuration(),directory,'hob','legacy-hob')
