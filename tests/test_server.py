import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import structlog
from localthings.server import Device, Subscription
from fixtures import configuration, registration
from thingslocal.server import ApplianceServer
from thingslocal.settings import load_registry
from thingslocal.controls import ControlRejected


def status(config):
    return {'devices': {device['id']: {'registered': False} for device in config['devices']}}


def token(device):
    encode = lambda value: base64.b64encode(json.dumps(value).encode()).decode()
    return '.'.join((encode({'kid': device['identity']['serial']}),
                     encode({'mnId': device['identity']['manufacturer']}), base64.b64encode(b'test-signature').decode()))


class Writer:
    def __init__(self, address): self.address = address
    def get_extra_info(self, name): return (self.address, 45000)
    def is_closing(self): return False


class Connection:
    def __init__(self, address): self._writer = Writer(address); self._logger = structlog.get_logger()


class MultiDeviceServerTests(unittest.TestCase):
    def test_registering_and_replacing_one_device_preserves_the_other(self):
        config = configuration()
        with tempfile.TemporaryDirectory() as directory:
            server = ApplianceServer(config, directory, None, None, status(config), lambda: None)
            records = [registration(device) for device in config['devices']]
            for record in records: server.registerDevice(Device.fromDict(record))
            self.assertEqual(len(load_registry(Path(directory)/'registrations.json', config)), 2)
            restarted = ApplianceServer(config, directory, None, None, status(config), lambda: None)
            replacement = registration(config['devices'][0]); restarted.registerDevice(Device.fromDict(replacement))
            self.assertEqual(set(restarted._registeredDevices), {replacement['id'], records[1]['id']})
            saved = load_registry(Path(directory)/'registrations.json', config)
            self.assertEqual({row['id'] for row in saved}, {replacement['id'], records[1]['id']})

    def test_claims_from_one_registered_appliance_are_rejected_at_the_other_ip(self):
        config = configuration()
        with tempfile.TemporaryDirectory() as directory:
            server = ApplianceServer(config, directory, None, None, status(config), lambda: None)
            record = registration(config['devices'][0]); server.registerDevice(Device.fromDict(record))
            allowed = server._clientFactory(Connection(config['devices'][0]['ip']), record['id'], token(config['devices'][0]))
            self.assertIsNotNone(allowed)
            denied = server._clientFactory(Connection(config['devices'][1]['ip']), record['id'], token(config['devices'][0]))
            self.assertIsNone(denied)
            self.assertIsNone(server._clientFactory(Connection('192.168.30.99'), record['id'], token(config['devices'][0])))

    def test_failed_durable_registration_does_not_change_registry_or_reply_identity(self):
        config = configuration()
        with tempfile.TemporaryDirectory() as directory:
            server = ApplianceServer(config, directory, None, None, status(config), lambda: None)
            with patch('thingslocal.server.atomic_json', side_effect=OSError('disk full')):
                with self.assertRaises(OSError): server.registerDevice(Device.fromDict(registration(config['devices'][0])))
            self.assertEqual(server._registeredDevices, {})
            self.assertFalse((Path(directory)/'registrations.json').exists())

    def test_malformed_registration_cannot_poison_the_next_restart(self):
        config = configuration()
        with tempfile.TemporaryDirectory() as directory:
            server = ApplianceServer(config, directory, None, None, status(config), lambda: None)
            original = registration(config['devices'][0]); server.registerDevice(Device.fromDict(original))
            before = (Path(directory)/'registrations.json').read_bytes()
            # A second appliance must pass the same schema used for restoration.
            for key, value in (('lookupId', None), ('vid', ''), ('dip', [])):
                bad = registration(config['devices'][1]); bad[key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    server.registerDevice(Device.fromDict(bad))
                self.assertEqual((Path(directory)/'registrations.json').read_bytes(), before)
                self.assertEqual(set(server._registeredDevices), {original['id']})


class CommandBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_is_bound_to_enabled_appliance_ip_session_and_subscription(self):
        config = configuration(); selected = config['devices'][1]
        selected['controls'] = {'dishwasher_course': True}
        with tempfile.TemporaryDirectory() as directory:
            server = ApplianceServer(config, directory, None, None, status(config), lambda: None)
            record = registration(selected); server.registerDevice(Device.fromDict(record))
            connection = Connection(selected['ip']); connection._logger = connection._logger.bind(connid=11)
            connection._client = server._clientFactory(connection, record['id'], token(selected))
            connection._closeConnection = False
            connection._subscriptions = {Subscription('/v1/commands/' + record['id']): 1}
            connection.publish = AsyncMock(); server._connections.add(connection)
            self.assertTrue(server.command_ready(selected['id'], 11))
            self.assertFalse(server.command_ready(selected['id'], 10))
            self.assertFalse(server.command_ready(config['devices'][0]['id'], 11))
            await server.select_course(selected['id'], 11, 'auto')
            args, kwargs = connection.publish.await_args
            self.assertEqual(str(args[0]), '/v1/commands/' + record['id'])
            self.assertEqual(kwargs, {'qos': 1})
            command = args[1]['commands'][0]
            self.assertEqual((command['component'], command['capability'], command['command'], command['arguments']),
                ('main', 'samsungce.dishwasherWashingCourse', 'setWashingCourse', ['auto']))
            with self.assertRaises(ControlRejected): await server.select_course(selected['id'], 10, 'eco')
            connection.publish.assert_awaited_once()
            connection._writer.address = config['devices'][0]['ip']
            self.assertFalse(server.command_ready(selected['id'], 11))
            connection._writer.address = selected['ip']; connection._subscriptions = {}
            self.assertFalse(server.command_ready(selected['id'], 11))
