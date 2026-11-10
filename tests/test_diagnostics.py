from pathlib import Path
import tempfile
import unittest
from fixtures import event
from thingslocal.diagnostics import DishwasherDiagnostics


def alarms(items): return event('samsungce.errorAndAlarmState', 'events', items)
def count(value): return event('samsungce.drumSelfCleaning', 'washingCountAfterSelfClean', value)
def threshold(value): return event('samsungce.drumSelfCleaning', 'suggestionThreshold', value)


class DiagnosticsTests(unittest.TestCase):
    def test_later_clear_or_malformed_snapshot_in_one_batch_cannot_poison_outbox(self):
        for final in ([], [{'code': {}}]):
            with self.subTest(final=final), tempfile.TemporaryDirectory() as directory:
                model = DishwasherDiagnostics('example', directory); model.begin(1)
                model.accept(1, [alarms([{'code': '4C', 'triggeredTime': '2026-11-10T12:00:00Z'}]), alarms(final)])
                self.assertIsNone(model.notification)
                model.accept(1, [alarms([{'code': '5C', 'triggeredTime': '2026-11-10T12:01:00Z'}])])
                self.assertTrue(model.notification['message'])
                self.assertEqual(model.notification['codes'], ['5C'])

    def test_cleaning_needs_both_fresh_values_and_deduplicates_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            model = DishwasherDiagnostics('example', directory); model.begin(1)
            model.accept(1, [threshold(20)])
            self.assertNotIn('dishwasher_cleaning_due', model.values)
            self.assertIsNone(model.notification)
            model.accept(1, [count(20)])
            self.assertEqual(model.values['dishwasher_cleaning_due'], 'ON')
            note = model.notification
            self.assertEqual(note['kind'], 'cleaning_recommended')
            restarted = DishwasherDiagnostics('example', directory); restarted.begin(2)
            self.assertNotIn('dishwasher_cleaning_due', restarted.values)
            restarted.accept(2, [threshold(20), count(21)])
            self.assertEqual(len(restarted.journal['outbox']), 1)
            self.assertEqual(restarted.notification['id'], note['id'])
            restarted.accept(2, [count(0)]); restarted.accept(2, [count(20)])
            self.assertEqual(len(restarted.journal['outbox']), 2)

    def test_errors_and_cleaning_are_not_overwritten_before_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            model = DishwasherDiagnostics('example', directory); model.begin(1)
            model.accept(1, [alarms([{'code': '4C', 'description': 'Appliance report'}])])
            first = model.notification['id']
            model.accept(1, [threshold(20), count(20)])
            self.assertEqual(model.notification['id'], first)
            self.assertEqual(len(model.journal['outbox']), 2)
            model.acknowledge('not-the-head')
            self.assertEqual(model.notification['id'], first)
            model.acknowledge(first)
            self.assertEqual(model.notification['kind'], 'cleaning_recommended')
            restored = DishwasherDiagnostics('example', directory)
            self.assertEqual(restored.notification, model.notification)
            restored.acknowledge(restored.notification['id'])
            self.assertIsNone(restored.notification)
            self.assertEqual(model.path.stat().st_mode & 0o777, 0o600)

    def test_occurrence_timestamp_deduplicates_replay_after_empty_initial_report(self):
        with tempfile.TemporaryDirectory() as directory:
            model = DishwasherDiagnostics('example', directory); model.begin(1)
            occurrence = {'code': '4C', 'triggeredTime': '2026-11-10T12:00:00Z'}
            model.accept(1, [alarms([occurrence])])
            model.acknowledge(model.notification['id'])
            restarted = DishwasherDiagnostics('example', directory); restarted.begin(2)
            restarted.accept(2, [alarms([])]); restarted.accept(2, [alarms([occurrence])])
            self.assertIsNone(restarted.notification)
            restarted.accept(2, [alarms([dict(occurrence, triggeredTime='2026-11-10T13:00:00Z')])])
            self.assertIsNotNone(restarted.notification)

    def test_routine_messages_do_not_become_faults_and_unknown_codes_stay_neutral(self):
        with tempfile.TemporaryDirectory() as directory:
            model = DishwasherDiagnostics('example', directory); model.begin(1)
            model.accept(1, [alarms([{'code': 'DA_DW_DoorA_Opened'}])])
            self.assertEqual(model.values['dishwasher_attention'], 'OFF'); self.assertIsNone(model.notification)
            model.accept(1, [alarms([{'code': 'UNKNOWN_DEVICE_CODE'}])])
            self.assertIn('Device message', model.notification['message'])
            self.assertNotIn('fault', model.notification['message'].lower())
            original = model.notification['id']
            model.accept(1, [alarms([{'code': 'UNKNOWN_DEVICE_CODE'}])])
            self.assertEqual(len(model.journal['outbox']), 1)
            model.accept(1, [alarms([])])
            model.accept(1, [alarms([{'code': 'UNKNOWN_DEVICE_CODE'}])])
            self.assertEqual(len(model.journal['outbox']), 2)
            self.assertEqual(model.notification['id'], original)

    def test_unverified_message_codes_are_forwarded_and_explicit_error_type_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            model = DishwasherDiagnostics('example', directory); model.begin(1)
            model.accept(1, [alarms([{'code': 'DA_DW_M_0001'}, {'code': 'DA_DW_T_0001'}])])
            self.assertEqual(model.notification['codes'], ['DA_DW_M_0001', 'DA_DW_T_0001'])
            self.assertIn('Device message', model.notification['message'])
            model.accept(1, [alarms([{'code': 'DA_DW_DoorA_Opened', 'type': 'error'}])])
            self.assertIn('Appliance error', model.journal['outbox'][-1]['message'])

    def test_malformed_missing_old_session_and_zero_threshold_never_imply_no_problem(self):
        with tempfile.TemporaryDirectory() as directory:
            model = DishwasherDiagnostics('example', directory); model.begin(2)
            model.accept(1, [threshold(1), count(10), alarms([])])
            self.assertEqual(model.values, {})
            model.accept(2, [alarms([{'code': {}}]), threshold(0), count(5)])
            self.assertNotIn('dishwasher_attention', model.values)
            self.assertNotIn('dishwasher_cleaning_due', model.values)
            self.assertIsNone(model.notification)

    def test_outbox_remains_bounded_and_never_replaces_inflight_head(self):
        with tempfile.TemporaryDirectory() as directory:
            model = DishwasherDiagnostics('example', directory); model.begin(1)
            for number in range(70): model.accept(1, [alarms([{'code': 'CODE_' + str(number)}])])
            self.assertEqual(len(model.journal['outbox']), 64)
            self.assertEqual(model.notification['codes'], ['CODE_0'])
            self.assertIn('CODE_69', model.journal['outbox'][-1]['message'])

    def test_foreign_journal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            model = DishwasherDiagnostics('one', directory); model.begin(1)
            model.accept(1, [alarms([{'code': '4C'}])])
            other = Path(directory) / 'notifications-two.json'; other.write_bytes(model.path.read_bytes())
            with self.assertRaises(ValueError): DishwasherDiagnostics('two', directory)
