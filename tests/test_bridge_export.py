import json
from pathlib import Path
import tempfile
import unittest
from fixtures import configuration, event
from thingslocal.bridge import MQTTBridge, discovery_messages
from thingslocal.cli import export_runtime
from thingslocal.settings import device_identifier
from thingslocal.telemetry import DeviceTelemetry


class Client:
    def __init__(self): self.messages = []
    def publish(self, topic, payload, qos, retain): self.messages.append((topic, payload, qos, retain))


class BridgeTests(unittest.TestCase):
    def test_discovery_for_two_devices_is_isolated_and_only_read_only(self):
        config = configuration(); topics = []
        for device in config['devices']:
            state = DeviceTelemetry(device); state.begin(1); state.accept(1,[event('switch','switch','on')])
            bridge = MQTTBridge(config, device, '2026-12-10T23:59:59+00:00')
            bridge.update(state.values, True, state.entities); client = Client(); bridge._flush(client,-1)
            own = [topic for topic, payload, _, _ in client.messages if topic.startswith('homeassistant/')]
            topics.append(set(own))
            for topic,payload,_,_ in client.messages:
                if not topic.startswith('homeassistant/') or not payload: continue
                value = json.loads(payload)
                self.assertIn(topic.split('/')[1],('sensor','binary_sensor'))
                self.assertNotIn('command_topic', payload)
                if 'availability' in value:
                    self.assertEqual(value['availability_mode'],'all')
                    self.assertIn('in value_json',value['availability'][2]['value_template'])
        self.assertFalse(topics[0] & topics[1])

    def test_outage_is_coalesced_without_replaying_old_online_states(self):
        config = configuration(); bridge = MQTTBridge(config,config['devices'][0],'expiry')
        for value in range(5000): bridge.update({'test':value},True,{})
        bridge.update({},False,{})
        client=Client(); bridge._flush(client,-1)
        self.assertEqual([payload for topic,payload,*_ in client.messages if topic.endswith('/device_availability')],['offline'])
        self.assertEqual([payload for topic,payload,*_ in client.messages if topic.endswith('/state')],['{}'])

    def test_runtime_export_never_contains_wifi_tokens_keys_or_old_registration(self):
        config=configuration()
        config['devices'][0]['wifi'] = {'password': 'DO-NOT-COPY-NESTED-WIFI'}
        config['devices'][0]['identity']['setup_key'] = 'DO-NOT-COPY-SETUP-KEY'
        config['broker']['oauth_token'] = 'DO-NOT-COPY-TOKEN'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); workspace=root/'setup'; workspace.mkdir()
            cert=root/'source.crt';key=root/'source.key';cert.write_text('fixture-cert');key.write_text('fixture-key')
            config['broker'].update(certificate=str(cert),private_key=str(key))
            (workspace/'samsung-cli-credentials.json').write_text('DO-NOT-COPY-TOKEN')
            (workspace/'registrations.json').write_text('DO-NOT-COPY-REGISTRY')
            output=root/'export'; result=export_runtime(workspace,config,output,'/share/thingslocal-test')
            text=(output/'config.json').read_text()
            self.assertNotIn(config['wifi']['password'],text)
            self.assertNotIn('DO-NOT-COPY',text)
            self.assertFalse(result['registry_included'])
            self.assertEqual({item.name for item in output.iterdir()},{'config.json','server.crt','server.key'})
            self.assertEqual((output/'server.key').stat().st_mode & 0o777,0o600)
            saved=json.loads(text);self.assertEqual(saved['broker']['certificate'],'/share/thingslocal-test/server.crt')
