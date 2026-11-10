import asyncio
import json
import unittest
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fixtures import configuration, event
from thingslocal.bridge import MQTTBridge
from thingslocal.controls import DishwasherCourseControl, course_enabled
from thingslocal.settings import runtime_config, validate


def configured_device():
    device = configuration()['devices'][1]
    device['controls'] = {'dishwasher_course': True}
    return device


def ready(control, session=1):
    control.begin(session)
    control.accept(session, [event('switch', 'switch', 'on'),
        event('samsungce.dishwasherOperation', 'operatingState', 'ready'),
        event('samsungce.dishwasherJobState', 'dishwasherJobState', 'none'),
        event('samsungce.dishwasherWashingCourse', 'washingCourse', 'eco'),
        event('samsungce.dishwasherWashingCourse', 'supportedCourses', ['eco', 'auto'])])


class CourseTests(unittest.IsolatedAsyncioTestCase):
    def control(self, **kwargs):
        return DishwasherCourseControl(configured_device(), lambda: None, lambda session: True, **kwargs)

    async def test_only_fresh_matching_device_report_confirms(self):
        control = self.control(); ready(control)
        send = AsyncMock()
        task = asyncio.create_task(control.select('auto', send)); await asyncio.sleep(0)
        self.assertFalse(control.snapshot()['available'])
        self.assertEqual(control.snapshot()['value'], 'eco')
        control.accept(0, [event('samsungce.dishwasherWashingCourse', 'washingCourse', 'auto')])
        self.assertFalse(task.done())
        control.accept(1, [event('samsungce.dishwasherWashingCourse', 'washingCourse', 'auto')])
        await task
        self.assertEqual(control.result, 'confirmed'); self.assertTrue(control.snapshot()['available'])
        send.assert_awaited_once_with(1, 'auto')

    async def test_busy_off_unsupported_and_unchanged_do_not_write(self):
        for cap, attr, value in [('switch', 'switch', 'off'),
                ('samsungce.dishwasherOperation', 'operatingState', 'running'),
                ('samsungce.dishwasherJobState', 'dishwasherJobState', 'washing')]:
            control = self.control(); ready(control); send = AsyncMock()
            control.accept(1, [event(cap, attr, value)])
            await control.select('auto', send); send.assert_not_awaited()
        for course in ('unsupported', 'eco'):
            control = self.control(); ready(control); send = AsyncMock()
            await control.select(course, send); send.assert_not_awaited()

    async def test_pending_request_has_no_command_queue(self):
        control = self.control(); ready(control); send = AsyncMock()
        task = asyncio.create_task(control.select('auto', send)); await asyncio.sleep(0)
        await control.select('eco', send)
        send.assert_awaited_once_with(1, 'auto')
        control.accept(1, [event('samsungce.dishwasherWashingCourse', 'washingCourse', 'auto')])
        await task

    async def test_timeout_is_unknown_and_blocks_until_a_new_course_report(self):
        control = self.control(timeout=0.001); ready(control); send = AsyncMock()
        await control.select('auto', send)
        self.assertEqual(control.result, 'timeout_unknown'); self.assertFalse(control.snapshot()['available'])
        await control.select('auto', send); send.assert_awaited_once()
        control.accept(1, [event('samsungce.dishwasherWashingCourse', 'washingCourse', 'eco')])
        self.assertTrue(control.snapshot()['available'])

    async def test_reconnect_interrupts_pending_and_discards_old_readiness(self):
        control = self.control(); ready(control); send = AsyncMock()
        task = asyncio.create_task(control.select('auto', send)); await asyncio.sleep(0)
        control.begin(2); await task
        control.accept(1, [event('samsungce.dishwasherWashingCourse', 'washingCourse', 'auto')])
        self.assertEqual(control.snapshot()['options'], []); self.assertFalse(control.snapshot()['available'])
        send.assert_awaited_once()

    async def test_missing_or_malformed_options_and_missing_subscription_disable(self):
        control = self.control(); ready(control)
        control.accept(1, [event('samsungce.dishwasherWashingCourse', 'supportedCourses', ['eco', {}])])
        self.assertFalse(control.snapshot()['available'])
        ready(control); control.connection_ready = lambda session: False
        self.assertFalse(control.snapshot()['available'])


class ControlConfigAndBridgeTests(unittest.TestCase):
    def test_notification_uuid_is_not_republished_on_ordinary_telemetry(self):
        bridge = self.bridge(); messages = []
        client = SimpleNamespace(publish=lambda topic, payload, **kw: messages.append((topic, payload)),
                                 subscribe=lambda *args, **kwargs: None)
        note = {'id': str(uuid4()), 'message': 'Example', 'kind': 'device_message',
                'codes': ['EXAMPLE'], 'created': '2026-11-10T12:00:00+00:00'}
        bridge.update_notification(note); revision = bridge._flush(client, -1)
        for number in range(3):
            bridge.update_notification(note); bridge.update({'example': number}, True, {})
            revision = bridge._flush(client, revision)
        sent = lambda: [message for message in messages if message[0].endswith('/notification_event')]
        self.assertEqual(len(sent()), 1)
        bridge._on_connect(client, None, None, 0, None); bridge._flush(client, revision)
        self.assertEqual(len(sent()), 2)

    def test_notification_ack_is_bounded_and_bound_to_outbox_head_even_if_retained(self):
        bridge = self.bridge(); calls = []; bridge.set_acknowledgement_handler(calls.append)
        identifier = str(uuid4()); bridge.update_notification({'id': identifier})
        def ack(value):
            bridge._on_message(None, None, SimpleNamespace(topic=bridge.base + '/notification_ack',
                payload=value.encode(), retain=True, qos=1))
        ack(str(uuid4())); ack('none'); self.assertEqual(calls, [])
        ack(identifier); ack(identifier); self.assertEqual(calls, [identifier])
        bridge.update_notification(None); bridge.acknowledgement_done(); ack(identifier)
        self.assertEqual(calls, [identifier])

    def test_controls_default_off_and_cannot_be_enabled_for_cooktop(self):
        config = configuration()
        self.assertFalse(any(course_enabled(d) for d in config['devices']))
        for controls in ({'dishwasher_course': 'true'}, {'start': True}):
            config['devices'][1]['controls'] = controls
            with self.assertRaises(ValueError): validate(config)
        config = configuration(); config['devices'][0]['controls'] = {'dishwasher_course': True}
        with self.assertRaises(ValueError): validate(config)
        config = configuration(); config['devices'][1]['controls'] = {'dishwasher_course': True}
        self.assertTrue(runtime_config(validate(config))['devices'][1]['controls']['dishwasher_course'])

    def bridge(self):
        config = configuration(); device = configured_device()
        bridge = MQTTBridge(config, device, 'expiry')
        bridge.update_control({'options': ['eco', 'auto'], 'value': 'eco', 'available': True,
                               'result': 'ready', 'session': 5})
        bridge._accept_commands = True
        return bridge

    def test_retained_bad_payload_other_topic_and_repeated_pending_never_dispatch(self):
        bridge = self.bridge(); calls = []; bridge.set_command_handler(lambda *args: calls.append(args))
        def message(**kwargs):
            values = dict(topic=bridge.command_topic, payload=b'auto', retain=False, qos=0)
            values.update(kwargs); bridge._on_message(None, None, SimpleNamespace(**values))
        message(retain=True); message(qos=1); message(payload=b'\xff'); message(payload=b'start')
        message(topic=bridge.base + '/command/start')
        self.assertEqual(calls, [])
        message(); message(); self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ('auto', 0, 5))
        bridge._on_disconnect(None, None, None, None, None)
        self.assertFalse(bridge.command_current(0))

    def test_select_discovery_is_non_optimistic_and_has_device_programs_only(self):
        bridge = self.bridge(); messages = []
        client = SimpleNamespace(publish=lambda topic, payload, **kw: messages.append((topic, payload)))
        bridge._flush(client, -1)
        configs = [json.loads(payload) for topic, payload in messages
                   if topic.startswith('homeassistant/select/') and payload]
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]['options'], ['eco', 'auto'])
        self.assertFalse(configs[0]['optimistic']); self.assertFalse(configs[0]['retain'])
        self.assertEqual(configs[0]['qos'], 0)
        self.assertEqual(configs[0]['command_topic'], bridge.command_topic)
