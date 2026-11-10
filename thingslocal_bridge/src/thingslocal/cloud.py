"""Owner-initiated setup only. Routine telemetry never calls SmartThings APIs."""
import base64
import hashlib
import json
from pathlib import Path
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from .settings import atomic_json

API = 'https://api.smartthings.com/v1/'
BLOB = 'https://client.smartthings.com/identity/easysetup/blob'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl): return None


def serial_hash(serial):
    return base64.urlsafe_b64encode(hashlib.sha256(serial.encode()).digest()).decode()


def qr_identity(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'qr.samsungiots.com' or
            parsed.username or parsed.password or parsed.port not in (None, 443) or
            parsed.path not in ('', '/') or parsed.fragment):
        raise ValueError('Use the onboarding URL from your own Samsung/SmartThings QR code')
    query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
    if any(len(query.get(key, [])) != 1 for key in ('m', 's', 'r')):
        raise ValueError('The QR needs one manufacturer, setup ID and serial')
    manufacturer, setup_id, serial = (query[key][0] for key in ('m', 's', 'r'))
    if not re.fullmatch(r'[A-Za-z0-9]{4}', manufacturer) or not re.fullmatch(r'\d{3}', setup_id):
        raise ValueError('Invalid QR manufacturer or setup ID')
    if not re.fullmatch(r'[A-Za-z0-9_-]{4,128}', serial): raise ValueError('Invalid device serial')
    return {'manufacturer': manufacturer, 'setup_id': setup_id, 'serial': serial}


def pairing_identity(ssid):
    match = re.fullmatch(r'(?P<kind>[^\r\n]{1,40})_E(?P<version>[45])(?P<manufacturer>[A-Za-z0-9]{4})(?P<setup>\d{3})(?P<suffix>[A-Za-z0-9_-]+)', ssid)
    if match is None: return None
    suffix = match['suffix']
    if match['version'] == '4':
        if len(suffix) != 9 or suffix[0] not in '67': return None
        suffix = suffix[1:]
    elif len(suffix) != 8: return None
    return {'manufacturer': match['manufacturer'], 'setup_id': match['setup'],
            'hybrid_serial': suffix, 'kind': match['kind']}


def matching_ap(ssid, identity):
    found = pairing_identity(ssid)
    return (found is not None and found['manufacturer'] == identity['manufacturer'] and
            found['setup_id'] == identity['setup_id'] and
            found['hybrid_serial'] == serial_hash(identity['serial'])[:4] + identity['serial'][-4:])


def read_token(workspace, token_file=None):
    if token_file is not None:
        token = Path(token_file).read_text().strip()
    else:
        credentials = json.loads((Path(workspace) / 'samsung-cli-credentials.json').read_text())
        token = credentials['thingslocal']['accessToken']
    if not isinstance(token, str) or not token or '\n' in token: raise ValueError('Run thingslocal login first')
    return token


def request(url, token, payload=None):
    if not (url == BLOB or url.startswith(API + 'devices')): raise ValueError('Unexpected setup API endpoint')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        headers['Accept'] = 'application/vnd.smartthings+json;v=2'
    req = urllib.request.Request(url, headers=headers,
        data=None if payload is None else json.dumps(payload).encode())
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=20) as response: return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try: body = json.loads(error.read(1048576))
        except ValueError: body = {}
        return error.code, body


def devices(token):
    status, result = request(API + 'devices', token)
    if status != 200: raise ValueError(f'SmartThings device lookup returned HTTP {status}; run login again if expired')
    return [{'device_id': item['deviceId'], 'name': item.get('label', item.get('name', 'Appliance')),
             'transport': item.get('type'), 'manufacturer': item.get('manufacturerName')}
            for item in result.get('items', [])]


def acquire_material(workspace, device, token):
    identity = device['identity']
    nonce = base64.urlsafe_b64encode(secrets.token_hex(32).encode()).decode()
    payload = {'mnId': identity['manufacturer'], 'setupId': identity['setup_id'],
               'keyid': {'type': 'hybrid_sn', 'value': serial_hash(identity['serial'])[:4] + identity['serial'][-4:]},
               'rand': nonce}
    status, result = request(BLOB, token, payload)
    if status != 200:
        atomic_json(Path(workspace) / 'last-cloud-setup-error.json', {'status': status, 'response': result})
        raise ValueError(f'EasySetup returned HTTP {status}. A Samsung user identity is required; a generic HA/PAT token may be insufficient. See the private error file.')
    matches = [item for item in result.get('data', []) if isinstance(item, dict) and item.get('sn') == serial_hash(identity['serial'])]
    if len(matches) != 1 or matches[0].get('type') != 'ECDH_ED25519':
        raise ValueError('EasySetup material does not match the selected serial')
    item = matches[0]
    for key in ('cloudPubKey', 'blob'):
        if len(base64.b64decode(item[key], altchars=b'-_', validate=True)) != 32:
            raise ValueError('Invalid EasySetup key size')
    material = {'identity': identity, 'nonce': nonce,
                'public_key': item['cloudPubKey'], 'shared_key': item['blob']}
    atomic_json(Path(workspace) / 'setup-material' / (device['id'] + '.json'), material)
    return {'saved': True, 'device': device['id'], 'identity_hash_verified': True}
