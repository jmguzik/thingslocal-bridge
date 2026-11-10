import unittest
from fixtures import configuration, event
from thingslocal.telemetry import DeviceTelemetry, field_key


class TelemetryTests(unittest.TestCase):
    def test_cooktop_and_dishwasher_have_independent_values_and_sessions(self):
        config = configuration(); hob = DeviceTelemetry(config['devices'][0]); washer = DeviceTelemetry(config['devices'][1])
        hob.begin(1); washer.begin(2)
        hob.accept(1, [event('samsungce.cooktopHeatingPower', 'manualLevel', 6, 'burner-03')])
        washer.accept(2, [event('dishwasherOperatingState', 'machineState', 'run'),
                          event('samsungce.dishwasherOperation', 'remainingTime', 42, unit='min')])
        self.assertEqual(list(hob.values.values()), [6])
        self.assertEqual(set(washer.values.values()), {'run', 42})
        self.assertFalse(hob.disconnect(2)); self.assertTrue(hob.online)
        hob.begin(3); self.assertEqual(hob.values, {})
        self.assertFalse(hob.accept(1, [event('switch', 'switch', 'off')]))
        self.assertEqual(set(washer.values.values()), {'run', 42})

    def test_unknown_attributes_are_catalogued_without_their_values(self):
        device = configuration()['devices'][1]; state = DeviceTelemetry(device); state.begin(1)
        state.accept(1, [event('samsungce.deviceIdentification', 'serialNumber', 'DO-NOT-PUBLISH'),
                         event('samsungce.remoteManagementData', 'reportRawData', 'DO-NOT-PUBLISH'),
                         event('vendor.custom', 'status', 'PRIVATE-CUSTOM')])
        self.assertEqual(state.values, {}); self.assertEqual(state.entities, {})
        self.assertEqual(len(state.catalog), 3)
        self.assertNotIn('DO-NOT-PUBLISH', str(state.catalog))
        self.assertNotIn('PRIVATE-CUSTOM', str(state.catalog))

    def test_other_device_can_opt_into_one_explicit_scalar_attribute(self):
        device = configuration()['devices'][1]; device['profile'] = 'generic'
        device['entities'] = [{'component': 'main', 'capability': 'vendor.custom',
                              'attribute': 'status', 'name': 'Custom status', 'platform': 'sensor'}]
        state = DeviceTelemetry(device); state.begin(1)
        state.accept(1, [event('vendor.custom', 'status', 'ready'), event('vendor.custom', 'other', 'not allowed')])
        self.assertEqual(list(state.values.values()), ['ready']); self.assertEqual(len(state.entities), 1)

    def test_unknown_operating_state_cannot_leave_old_heating_on(self):
        state = DeviceTelemetry(configuration()['devices'][0]); state.begin(1)
        state.accept(1, [event('custom.cooktopOperatingState', 'cooktopOperatingState', 'run')])
        self.assertEqual(state.values['heating'], 'ON')
        state.accept(1, [event('custom.cooktopOperatingState', 'cooktopOperatingState', 'paused')])
        self.assertEqual(state.values['heating'], 'OFF')
        state.accept(1, [event('custom.cooktopOperatingState', 'cooktopOperatingState', 'unrecognized')])
        self.assertNotIn('heating', state.values)

    def test_invalid_new_values_invalidate_fields_and_optional_components_are_configurable(self):
        state = DeviceTelemetry(configuration()['devices'][0]); state.begin(1)
        value = event('samsungce.cooktopHeatingPower', 'manualLevel', 4, 'burner-05')
        state.accept(1, [value]); key = field_key('burner-05', value['capability'], value['attribute'])
        self.assertFalse(state.entities[key].options['enabled_by_default'])
        state.accept(1, [{**value, 'value': False}]); self.assertNotIn(key, state.values)
        state.accept(1, [None, 'invalid', {**value, 'unit': {}, 'value': 2}])
        self.assertEqual(state.values[key], 2)
