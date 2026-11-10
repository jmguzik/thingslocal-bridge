from uuid import uuid4


def configuration():
    return {
        'installation_id': 'aabbccddeeff', 'timezone': 'UTC',
        'broker': {'hostname': 'bridge.example.test', 'lan_ip': '192.168.30.10', 'port': 8883,
                   'certificate': '/tmp/example.crt', 'private_key': '/tmp/example.key'},
        'mqtt': {'host': 'core-mosquitto', 'port': 1883, 'username': 'test', 'password': 'test-only', 'namespace': 'thingslocal'},
        'wifi': {'ssid': 'Example_IoT', 'password': 'fixture-only-wifi-password'},
        'devices': [
            {'id': 'hob', 'name': 'Example hob', 'ip': '192.168.30.20', 'label': 'hob', 'profile': 'cooktop',
             'identity': {'serial': 'TESTHOB0001', 'manufacturer': '0A4H', 'model': 'TEST-HOB', 'setup_id': '001'},
             'entities': [], 'optional_components': ['burner-05', 'burner-06']},
            {'id': 'washer', 'name': 'Example dishwasher', 'ip': '192.168.30.21', 'label': 'washer', 'profile': 'dishwasher',
             'identity': {'serial': 'TESTWASH0002', 'manufacturer': '0AwG', 'model': 'TEST-WASHER', 'setup_id': '000'},
             'entities': [], 'optional_components': []},
        ],
    }


def registration(device):
    return {'id': str(uuid4()), 'serialNumber': device['identity']['serial'], 'mnId': device['identity']['manufacturer'],
            'name': device['label'], 'lookupId': str(uuid4()), 'vid': 'fixture-profile',
            'dip': {'id': str(uuid4()), 'majorVersion': 1, 'minorVersion': 0}}


def event(capability, attribute, value, component='main', **kwargs):
    return {'component': component, 'capability': capability, 'attribute': attribute, 'value': value, **kwargs}
