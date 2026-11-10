"""Explicit read-only profiles and per-session, per-attribute availability."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import math
import re


@dataclass(frozen=True)
class Entity:
    key: str
    name: str
    platform: str = 'sensor'
    options: dict = field(default_factory=dict)
    attributes_key: str | None = None

    def dictionary(self): return asdict(self)


def field_key(component, capability, attribute):
    return 'f_' + hashlib.sha256('/'.join((component, capability, attribute)).encode()).hexdigest()[:16]


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def scalar(value):
    return ((isinstance(value, str) and 0 < len(value) <= 256) or
            number(value) or type(value) is bool)


def parsed(value, kind):
    if kind == 'number': return value if number(value) else None
    if kind == 'level': return value if type(value) is int and 0 <= value <= 1000000 else None
    if kind == 'percent': return value if number(value) and 0 <= value <= 100 else None
    if kind == 'text': return value if isinstance(value, str) and 0 < len(value) <= 256 else None
    if kind == 'scalar': return value if scalar(value) else None
    if kind == 'binary':
        if value in ('on', 'ON', 'open', 'locked') or value is True: return 'ON'
        if value in ('off', 'OFF', 'closed', 'unlocked') or value is False: return 'OFF'
        return None
    if kind == 'timestamp':
        if not isinstance(value, str): return None
        try: stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError: return None
        return stamp.isoformat() if stamp.tzinfo is not None else None
    return None


COMMON = {
    ('switch', 'switch'): ('Power', 'binary', {}),
    ('temperatureMeasurement', 'temperature'): ('Temperature', 'number', {'device_class': 'temperature'}),
    ('relativeHumidityMeasurement', 'humidity'): ('Humidity', 'percent', {'device_class': 'humidity', 'unit_of_measurement': '%'}),
    ('battery', 'battery'): ('Battery', 'percent', {'device_class': 'battery', 'unit_of_measurement': '%'}),
    ('contactSensor', 'contact'): ('Contact', 'binary', {'device_class': 'opening'}),
    ('powerMeter', 'power'): ('Power consumption', 'number', {'device_class': 'power'}),
    ('energyMeter', 'energy'): ('Energy', 'number', {'device_class': 'energy'}),
}
DISHWASHER = {
    ('dishwasherOperatingState', 'machineState'): ('Machine state', 'text', {}),
    ('dishwasherOperatingState', 'dishwasherJobState'): ('Job state', 'text', {}),
    ('dishwasherOperatingState', 'progress'): ('Progress', 'percent', {'unit_of_measurement': '%'}),
    ('dishwasherOperatingState', 'completionTime'): ('Expected completion', 'timestamp', {'device_class': 'timestamp'}),
    ('samsungce.dishwasherOperation', 'operatingState'): ('Operating state', 'text', {}),
    ('samsungce.dishwasherOperation', 'progressPercentage'): ('Operation progress', 'percent', {'unit_of_measurement': '%'}),
    ('samsungce.dishwasherOperation', 'remainingTime'): ('Remaining time', 'number', {}),
    ('samsungce.dishwasherOperation', 'remainingTimeStr'): ('Remaining time display', 'text', {}),
    ('samsungce.dishwasherOperation', 'operationTime'): ('Operation time', 'number', {}),
    ('samsungce.dishwasherOperation', 'timeLeftToStart'): ('Time until start', 'number', {}),
    ('samsungce.dishwasherWashingCourse', 'washingCourse'): ('Washing course', 'scalar', {}),
    ('samsungce.dishwasherJobState', 'dishwasherJobState'): ('Detailed job state', 'text', {}),
    ('custom.dishwasherOperatingProgress', 'dishwasherOperatingProgress'): ('Operating progress', 'scalar', {}),
}
UNITS = {'C': '°C', 'F': '°F', '°C': '°C', '°F': '°F', 'W': 'W', 'kW': 'kW',
         'Wh': 'Wh', 'kWh': 'kWh', 'min': 'min', 's': 's', '%': '%'}


class DeviceTelemetry:
    def __init__(self, config):
        self.config = config
        self.session = None
        self.values, self.entities, self.catalog = {}, {}, {}

    @property
    def online(self): return self.session is not None

    def begin(self, session):
        self.session = session; self.values = {}

    def disconnect(self, session):
        if session is not None and self.session == session:
            self.session = None; return True
        return False

    def _mapping(self, component, capability, attribute):
        pair = capability, attribute
        rule = COMMON.get(pair)
        if self.config['profile'] == 'dishwasher': rule = DISHWASHER.get(pair, rule)
        if self.config['profile'] == 'cooktop':
            if pair == ('custom.cooktopOperatingState', 'cooktopOperatingState'):
                rule = ('Operating state', 'text', {})
            elif pair == ('samsungce.kidsLockControl', 'lockState'):
                rule = ('Panel lock', 'binary', {'icon': 'mdi:lock'})
            elif pair == ('samsungce.cooktopHeatingPower', 'manualLevel'):
                rule = ('Level', 'level', {'icon': 'mdi:stove'})
            elif pair == ('samsungce.cooktopHeatingPower', 'heatingMode'):
                rule = ('Heating mode', 'text', {})
            elif pair == ('samsungce.countDownTimer', 'currentValue'):
                rule = ('Remaining timer', 'number', {'entity_category': 'diagnostic'})
            elif pair == ('samsungce.surfaceResidualHeat', 'surfaceResidualHeat'):
                rule = ('Residual heat', 'text', {'entity_category': 'diagnostic'})
        for custom in self.config.get('entities', []):
            if (component, capability, attribute) == tuple(custom[key] for key in ('component', 'capability', 'attribute')):
                kind = 'binary' if custom.get('platform') == 'binary_sensor' else 'scalar'
                extra = {key: custom[key] for key in ('unit_of_measurement', 'device_class', 'entity_category') if key in custom}
                rule = custom.get('name', attribute), kind, extra
        return rule

    def accept(self, session, events):
        if self.session is None or session != self.session or not isinstance(events, list): return False
        changed = False
        for event in events:
            if not isinstance(event, dict): continue
            component, capability, attribute = (event.get(key) for key in ('component', 'capability', 'attribute'))
            if not all(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value)
                       for value in (component, capability, attribute)): continue
            key = field_key(component, capability, attribute)
            if key in self.catalog or len(self.catalog) < 512:
                value = event.get('value'); unit = event.get('unit')
                previous_unit = self.catalog.get(key, {}).get('unit')
                self.catalog[key] = {'component': component, 'capability': capability,
                                     'attribute': attribute, 'type': type(value).__name__,
                                     'unit': unit if isinstance(unit, str) and len(unit) <= 16 else previous_unit}
            rule = self._mapping(component, capability, attribute)
            if rule is None: continue
            if key not in self.entities and len(self.entities) >= 128: continue
            name, kind, extra = rule; extra = dict(extra)
            if component in self.config.get('optional_components', []): extra['enabled_by_default'] = False
            if component != 'main': name = component + ' ' + name
            if isinstance(event.get('unit'), str) and event['unit'] in UNITS:
                extra.setdefault('unit_of_measurement', UNITS[event['unit']])
            elif key in self.entities and 'unit_of_measurement' in self.entities[key].options:
                extra.setdefault('unit_of_measurement', self.entities[key].options['unit_of_measurement'])
            # HA requires a valid unit for these numeric device classes.
            if extra.get('device_class') in ('temperature', 'power', 'energy') and 'unit_of_measurement' not in extra:
                extra.pop('device_class')
            if extra.get('unit_of_measurement') in ('min', 's') and kind == 'number': extra.setdefault('device_class', 'duration')
            self.entities[key] = Entity(key, name, 'binary_sensor' if kind == 'binary' else 'sensor', extra)
            value = parsed(event.get('value'), kind)
            if value is None: self.values.pop(key, None)
            else: self.values[key] = value
            changed = True
            if capability == 'custom.cooktopOperatingState' and attribute == 'cooktopOperatingState':
                derived = 'heating'
                self.entities[derived] = Entity(derived, 'Heating', 'binary_sensor', {'device_class': 'heat'})
                if value == 'run': self.values[derived] = 'ON'
                elif value in ('ready', 'paused', 'finished', 'off', 'idle'): self.values[derived] = 'OFF'
                else: self.values.pop(derived, None)
        return changed
