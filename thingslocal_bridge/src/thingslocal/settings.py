"""Installation-specific configuration; no owner, network or model defaults."""

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import UUID
from zoneinfo import ZoneInfo


SLUG = re.compile(r'[a-z][a-z0-9_-]{0,39}\Z')
HOSTNAME = re.compile(r'(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}\Z')
PROFILES = ('generic', 'cooktop', 'dishwasher')


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, indent=2, allow_nan=False); stream.write('\n')
            stream.flush(); os.fsync(stream.fileno()); os.replace(temporary, path)
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally: temporary.unlink(missing_ok=True)


def private_ip(value):
    address = ipaddress.ip_address(value)
    if (address.version != 4 or not address.is_private or address.is_loopback or
            address.is_unspecified or address.is_multicast or address.is_link_local):
        raise ValueError('Use a reserved private IPv4 LAN address')
    return str(address)


def validate(config):
    if not re.fullmatch(r'[a-f0-9]{12}', config.get('installation_id', '')):
        raise ValueError('Invalid installation ID; create a new workspace with init')
    broker = config['broker']
    if not HOSTNAME.fullmatch(broker['hostname']):
        raise ValueError('The broker needs your own fully qualified domain name')
    private_ip(broker['lan_ip'])
    if type(broker.get('port', 8883)) is not int or not 1 <= broker.get('port', 8883) <= 65535:
        raise ValueError('Invalid broker port')
    mqtt = config['mqtt']
    if not all(isinstance(mqtt.get(key), str) and mqtt[key] for key in ('host', 'username', 'password')):
        raise ValueError('HA MQTT hostname and credentials are required')
    if type(mqtt['port']) is not int or not 1 <= mqtt['port'] <= 65535:
        raise ValueError('Invalid HA MQTT port')
    if not SLUG.fullmatch(mqtt.get('namespace', 'thingslocal')):
        raise ValueError('Invalid MQTT namespace')
    ZoneInfo(config.get('timezone', 'UTC'))
    devices = config.get('devices', [])
    if not isinstance(devices, list) or len(devices) > 16:
        raise ValueError('Expected at most 16 configured appliances')
    seen_ids, seen_serials, seen_ips, seen_labels = set(), set(), set(), set()
    for device in devices:
        if not SLUG.fullmatch(device['id']): raise ValueError('Invalid device ID')
        private_ip(device['ip'])
        identity = device['identity']
        if not all(isinstance(identity.get(key), str) and 0 < len(identity[key]) <= 128
                   for key in ('serial', 'manufacturer', 'model')):
            raise ValueError('Each appliance needs its own serial, manufacturer and model')
        if not re.fullmatch(r'[A-Za-z0-9]{4}', identity['manufacturer']):
            raise ValueError('Use the four-character manufacturer code from the QR/AP')
        if not re.fullmatch(r'\d{3}', identity.get('setup_id', '')):
            raise ValueError('A verified three-digit setup ID is required; it is never guessed')
        if device['profile'] not in PROFILES: raise ValueError('Unknown readout profile')
        controls = device.get('controls', {})
        if (not isinstance(controls, dict) or set(controls) - {'dishwasher_course'}
                or any(type(value) is not bool for value in controls.values())):
            raise ValueError('Only the boolean dishwasher_course control is supported')
        if controls.get('dishwasher_course') and device['profile'] != 'dishwasher':
            raise ValueError('Program selection is supported only for dishwasher profiles')
        for value, seen in ((device['id'], seen_ids), (identity['serial'], seen_serials),
                            (device['ip'], seen_ips), (device['label'], seen_labels)):
            if value in seen: raise ValueError('Device IDs, serials, IPs and registration labels must be unique')
            seen.add(value)
        if not SLUG.fullmatch(device['label']): raise ValueError('Invalid registration label')
        if not isinstance(device.get('name'), str) or not 1 <= len(device['name']) <= 128:
            raise ValueError('A display name is required')
        if not isinstance(device.get('entities', []), list) or len(device.get('entities', [])) > 64:
            raise ValueError('Expected at most 64 explicitly allowed extra attributes per device')
        for entity in device.get('entities', []):
            if entity.get('platform', 'sensor') not in ('sensor', 'binary_sensor'):
                raise ValueError('Only read-only sensor entities are supported')
            for key in ('component', 'capability', 'attribute'):
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', entity[key]):
                    raise ValueError('Invalid explicit attribute mapping')
    return config


def read_config(workspace):
    return validate(json.loads((Path(workspace) / 'config.json').read_text()))


def device_identifier(config, device):
    digest = hashlib.sha256((device['identity']['manufacturer'] + ':' +
                             device['identity']['serial']).encode()).hexdigest()[:12]
    return 'thingslocal_' + config['installation_id'] + '_' + digest


def runtime_config(config):
    """Never send Samsung login tokens, EasySetup keys or Wi-Fi passwords to HA."""
    result = {'installation_id': config['installation_id'], 'timezone': config.get('timezone', 'UTC')}
    result['broker'] = {key: config['broker'][key] for key in (
        'hostname', 'lan_ip', 'listen_host', 'port', 'certificate', 'private_key', 'trusted_ca') if key in config['broker']}
    result['mqtt'] = {key: config['mqtt'][key] for key in (
        'host', 'port', 'username', 'password', 'namespace') if key in config['mqtt']}
    result['devices'] = []
    for original in config['devices']:
        device = {key: original[key] for key in ('id', 'name', 'ip', 'label', 'profile',
                  'vendor_name', 'optional_components') if key in original}
        device['controls'] = {key: original['controls'][key] for key in ('dishwasher_course',)
                              if key in original.get('controls', {})}
        device['identity'] = {key: original['identity'][key] for key in ('serial', 'manufacturer', 'model', 'setup_id')}
        device['entities'] = [{key: entity[key] for key in ('component', 'capability', 'attribute',
            'platform', 'name', 'unit_of_measurement', 'device_class', 'entity_category') if key in entity}
            for entity in original.get('entities', [])]
        result['devices'].append(device)
    return result


def load_registry(path, config):
    path = Path(path)
    if not path.exists(): return []
    return validate_registry(json.loads(path.read_text()), config)


def validate_registry(records, config):
    selected = {device['identity']['serial']: device for device in config['devices']}
    if not isinstance(records, list) or len(records) > len(selected):
        raise ValueError('Registry does not belong to this configured installation')
    serials, ids = set(), set()
    for record in records:
        if not isinstance(record, dict): raise ValueError('Invalid registration record')
        device = selected.get(record.get('serialNumber'))
        if (not device or record.get('mnId') != device['identity']['manufacturer'] or
                record.get('name') != device['label']):
            raise ValueError('Foreign or mismatched registration')
        identifier = record.get('id')
        if not isinstance(identifier, str): raise ValueError('Invalid registration ID')
        if str(UUID(identifier)) != identifier or identifier in ids or record['serialNumber'] in serials:
            raise ValueError('Duplicate or invalid registration')
        if not isinstance(record.get('dip'), dict) or not all(
                isinstance(record.get(key), str) and record[key] for key in ('lookupId', 'vid')):
            raise ValueError('Incomplete registration')
        serials.add(record['serialNumber']); ids.add(identifier)
    return records
