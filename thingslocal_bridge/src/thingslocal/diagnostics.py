"""Explicit dishwasher alerts and cleaning reminders; no appliance commands."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID, uuid4

from .settings import atomic_json
from .telemetry import Entity, number

CODE = re.compile(r'[A-Za-z0-9_.:-]{1,96}\Z')
ROUTINE_CODES = {'DA_DW_DoorA_Opened'}
COUNT = 'washingCountAfterSelfClean'
THRESHOLD = 'suggestionThreshold'


class DishwasherDiagnostics:
    def __init__(self, identifier, directory):
        self.identifier = identifier
        self.path = Path(directory) / ('notifications-' + identifier + '.json')
        self.session = None; self.alarms = None; self.count = self.threshold = None
        self.journal = {'identifier': identifier, 'active_codes': [], 'cleaning_due': None,
                        'seen_occurrences': [], 'outbox': []}
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            if (saved.get('identifier') != identifier or not isinstance(saved.get('active_codes'), list)
                    or len(saved['active_codes']) > 32
                    or not all(isinstance(code, str) and CODE.fullmatch(code) for code in saved['active_codes'])
                    or (saved.get('cleaning_due') is not None and type(saved['cleaning_due']) is not bool)):
                raise ValueError('Invalid dishwasher notification journal')
            if (not isinstance(saved.get('outbox'), list) or len(saved['outbox']) > 64
                    or not isinstance(saved.get('seen_occurrences'), list) or len(saved['seen_occurrences']) > 256
                    or not all(isinstance(item, str) and re.fullmatch(r'[a-f0-9]{64}', item)
                               for item in saved['seen_occurrences'])):
                raise ValueError('Invalid dishwasher notification outbox')
            for note in saved['outbox']:
                if (not isinstance(note, dict) or str(UUID(note['id'])) != note['id']
                        or note.get('kind') not in ('device_message', 'cleaning_recommended', 'attention')
                        or not isinstance(note.get('message'), str) or not note['message'].strip() or len(note['message']) > 4096
                        or not isinstance(note.get('codes'), list) or len(note['codes']) > 32
                        or not all(isinstance(code, str) and CODE.fullmatch(code) for code in note['codes'])
                        or not isinstance(note.get('created'), str)):
                    raise ValueError('Invalid dishwasher notification event')
            self.journal = {key: saved[key] for key in self.journal}
        self.entities = {
            'dishwasher_attention': Entity('dishwasher_attention', 'Attention message reported', 'binary_sensor', {'device_class': 'problem'}),
            'dishwasher_alarm_codes': Entity('dishwasher_alarm_codes', 'Device messages',
                options={'icon': 'mdi:alert-circle-outline'}, attributes_key='dishwasher_alarm_attributes'),
            'dishwasher_cleaning_due': Entity('dishwasher_cleaning_due', 'Cleaning recommended', 'binary_sensor', {'device_class': 'problem'}),
            'dishwasher_cleaning_count': Entity('dishwasher_cleaning_count', 'Cycles since cleaning',
                options={'entity_category': 'diagnostic'}),
            'dishwasher_cleaning_threshold': Entity('dishwasher_cleaning_threshold', 'Cleaning reminder threshold',
                options={'entity_category': 'diagnostic'}),
        }

    def begin(self, session):
        self.session = session; self.alarms = None; self.count = self.threshold = None

    def disconnect(self, session):
        if session == self.session: self.begin(None)

    @property
    def notification(self): return self.journal['outbox'][0] if self.journal['outbox'] else None

    def acknowledge(self, identifier):
        if self.notification is None or self.notification['id'] != identifier: return
        updated = dict(self.journal); updated['outbox'] = self.journal['outbox'][1:]
        atomic_json(self.path, updated); self.journal = updated

    @property
    def cleaning_due(self):
        if self.count is None or self.threshold is None or self.threshold <= 0: return None
        return self.count >= self.threshold

    @property
    def values(self):
        result = {}
        if self.alarms is not None:
            codes = [item['code'] for item in self.alarms]
            text = ', '.join(codes) or 'none'
            result.update(dishwasher_attention='ON' if codes else 'OFF',
                dishwasher_alarm_codes=text if len(text) <= 255 else text[:250] + '...',
                dishwasher_alarm_attributes={'messages': self.alarms})
        if self.count is not None: result['dishwasher_cleaning_count'] = self.count
        if self.threshold is not None: result['dishwasher_cleaning_threshold'] = self.threshold
        if self.cleaning_due is not None: result['dishwasher_cleaning_due'] = 'ON' if self.cleaning_due else 'OFF'
        return result

    def accept(self, session, events):
        if session is None or session != self.session or not isinstance(events, list): return
        occurrences = {}
        for event in events:
            if not isinstance(event, dict) or event.get('component') != 'main': continue
            capability, attribute, value = (event.get(key) for key in ('capability', 'attribute', 'value'))
            if capability == 'samsungce.errorAndAlarmState' and attribute == 'events':
                occurrences = {}
                if (not isinstance(value, list) or len(value) > 32
                        or any(not isinstance(item, dict) or not isinstance(item.get('code'), str)
                               or not CODE.fullmatch(item['code']) for item in value)):
                    self.alarms = None; continue
                filtered = {}
                for item in value:
                    code = item['code']
                    kind = item.get('type') if item.get('type') in ('error', 'alarm') else 'notice'
                    if code in ROUTINE_CODES and kind != 'error': continue
                    description = item.get('description', '')
                    if not isinstance(description, str): description = ''
                    filtered[code] = {'code': code, 'description': ' '.join(description.split())[:180], 'type': kind}
                    triggered = item.get('triggeredTime')
                    if isinstance(triggered, str) and len(triggered) <= 64:
                        try:
                            stamp = datetime.fromisoformat(triggered.replace('Z', '+00:00'))
                            if stamp.tzinfo is not None:
                                occurrences[code] = hashlib.sha256((code + ':' + stamp.isoformat()).encode()).hexdigest()
                        except ValueError: pass
                self.alarms = [filtered[code] for code in sorted(filtered)]
            elif capability == 'samsungce.drumSelfCleaning' and attribute in (COUNT, THRESHOLD):
                valid = number(value) and 0 <= value <= 1000000 and value == int(value)
                if attribute == COUNT: self.count = int(value) if valid else None
                else: self.threshold = int(value) if valid else None
        updated = dict(self.journal)
        old_codes = set(self.journal['active_codes'])
        codes = [item['code'] for item in self.alarms] if self.alarms is not None else None
        new_codes = set(codes or []) - old_codes if codes is not None else set()
        for code, occurrence in occurrences.items():
            if occurrence in self.journal['seen_occurrences']: new_codes.discard(code)
            else: new_codes.add(code)
        new_cleaning = self.cleaning_due is True and self.journal['cleaning_due'] is not True
        if codes is not None: updated['active_codes'] = codes
        if self.cleaning_due is not None: updated['cleaning_due'] = self.cleaning_due
        updated['seen_occurrences'] = list(dict.fromkeys(self.journal['seen_occurrences'] + list(occurrences.values())))[-256:]
        if new_codes or new_cleaning:
            messages = []
            for item in self.alarms or []:
                description = item['description'] or ('Rinse aid empty' if item['code'] == 'DA_DW_RinseA_Empty'
                    else 'Appliance error' if item['type'] == 'error' else 'Device message')
                messages.append(description + ' (' + item['code'] + ')')
            if new_cleaning:
                messages.append(f'Cleaning recommended: {self.count} cycles since cleaning; threshold {self.threshold}.')
            note = {'id': str(uuid4()),
                'kind': 'attention' if new_codes and new_cleaning else 'device_message' if new_codes else 'cleaning_recommended',
                'message': '\n'.join(messages)[:4096], 'codes': codes or [],
                'created': datetime.now(timezone.utc).isoformat()}
            outbox = list(self.journal['outbox'])
            if len(outbox) == 64:
                previous = outbox.pop()
                note['message'] = (note['message'] + '\nEarlier pending messages (combined):\n' + previous['message'])[:4096]
            outbox.append(note); updated['outbox'] = outbox
        if updated != self.journal:
            atomic_json(self.path, updated)
            self.journal = updated
