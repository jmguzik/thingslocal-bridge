import base64
import unittest
from unittest.mock import Mock
from fixtures import configuration
from thingslocal.cloud import serial_hash
from thingslocal.provision import SetupSession


class ProvisioningTests(unittest.TestCase):
    def session(self):
        device=configuration()['devices'][0]
        material={'identity':device['identity'],'nonce':'fixture-nonce',
                  'public_key':base64.urlsafe_b64encode(b'p'*32).decode(),
                  'shared_key':base64.urlsafe_b64encode(b'b'*32).decode()}
        return SetupSession('192.168.4.1',material,'UTC'),device

    def test_full_hash_mismatch_stops_before_key_exchange_or_wifi_write(self):
        session,device=self.session()
        session.request=Mock(side_effect=[{}, {'hashedSn':serial_hash('ANOTHER-DEVICE')}])
        with self.assertRaises(ValueError):session.verify_owner(device['identity'])
        self.assertEqual([call.args[0] for call in session.request.call_args_list],['/logs/systeminfo','/deviceinfo'])

    def test_owner_confirmation_uses_selected_device_not_a_fixed_serial(self):
        session,device=self.session()
        session.request=Mock(side_effect=[{}, {'hashedSn':serial_hash(device['identity']['serial']),
                                               'protocolVersion':'1.2.0','iv':base64.urlsafe_b64encode(b'i'*16).decode()},
                                         {'otmSupportFeatures':[1]},{}])
        session.verify_owner(device['identity'])
        payload=session.request.call_args_list[-1].args[1]
        self.assertEqual(payload,{'otmSupportFeature':1,'sn':device['identity']['serial']})
        self.assertTrue(session.request.call_args_list[-1].kwargs['encrypted'])

    def test_credentials_are_not_written_if_target_network_is_absent(self):
        session,device=self.session();session.request=Mock(return_value={'wifiScanInfo':[]});before=Mock()
        with self.assertRaises(ValueError):session.configure({'ssid':'Someone-Else-IoT','password':'fixture'},'ssl://bridge.example.test:8883','chosen-label',before)
        before.assert_not_called();self.assertEqual(len(session.request.call_args_list),1)

    def test_target_ssid_and_broker_are_provided_by_new_installation(self):
        session,device=self.session();session.request=Mock(side_effect=[
            {'wifiScanInfo':[{'ssid':'Someone-Else-IoT','authType':3,'bssid':'00:11:22:33:44:55','rssi':-40}]},
            {'lookupId':'fixture-lookup'},{}]);before=Mock()
        session.configure({'ssid':'Someone-Else-IoT','password':'fixture'},'ssl://bridge.example.test:8883','chosen-label',before)
        payload=session.request.call_args_list[1].args[1]
        self.assertEqual(payload['wifiCredential']['ssid'],'Someone-Else-IoT')
        self.assertEqual(payload['brokerUrl'],'ssl://bridge.example.test:8883')
        self.assertEqual(payload['deviceName'],'chosen-label');before.assert_called_once()
