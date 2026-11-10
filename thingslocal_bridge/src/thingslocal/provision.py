"""Generic SoftAP EasySetup 1.x onboarding. All appliance heating/control is absent."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

from .cloud import NoRedirect, serial_hash
from .network import APSession, scan
from .security import verify_certificate, verify_route
from .settings import atomic_json, private_ip


class SetupSession:
    def __init__(self, gateway, material, timezone_name, deadline=None):
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad, unpad
        self.AES, self.pad, self.unpad = AES, pad, unpad
        self.gateway = private_ip(gateway)
        self.material, self.timezone = material, timezone_name
        self.key = base64.b64decode(material['shared_key'], altchars=b'-_', validate=True)
        if len(self.key) != 32: raise ValueError('Invalid EasySetup key')
        self.iv = None; self.deadline = deadline or time.monotonic() + 80
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, endpoint, payload=None, encrypted=False, require_encrypted=False, optional=False):
        if time.monotonic() >= self.deadline: raise TimeoutError('Pairing deadline reached; restoring home Wi-Fi')
        if encrypted:
            cipher = self.AES.new(self.key, self.AES.MODE_CBC, iv=self.iv)
            payload = {'message': base64.urlsafe_b64encode(cipher.encrypt(self.pad(json.dumps(payload).encode(), 16))).decode()}
        request = urllib.request.Request('http://' + self.gateway + ':8888' + endpoint,
            data=None if payload is None else json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=4) as response: body = json.load(response)
        except urllib.error.HTTPError as error:
            if optional and endpoint == '/logs/systeminfo' and error.code == 500: return {}
            raise ValueError(f'Appliance setup endpoint returned HTTP {error.code}') from None
        envelope = isinstance(body, dict) and isinstance(body.get('message'), str)
        if (encrypted or require_encrypted) and not envelope: raise ValueError('Expected an encrypted setup response')
        if envelope:
            if self.iv is None: raise ValueError('No setup encryption context')
            cipher = self.AES.new(self.key, self.AES.MODE_CBC, iv=self.iv)
            body = json.loads(self.unpad(cipher.decrypt(base64.urlsafe_b64decode(body['message'])), 16))
        if not isinstance(body, dict): raise ValueError('Invalid setup response')
        return body

    def verify_owner(self, identity):
        self.request('/logs/systeminfo', optional=True)
        info = self.request('/deviceinfo')
        if info.get('hashedSn') != serial_hash(identity['serial']):
            raise ValueError('The AP does not match the full selected serial hash')
        if not str(info.get('protocolVersion', '')).startswith('1.'):
            raise ValueError('Only the documented EasySetup 1.x protocol is supported')
        self.iv = base64.urlsafe_b64decode(info['iv'])
        if len(self.iv) != 16: raise ValueError('Invalid setup IV')
        clock = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H.%M.%S UTC')
        encoded = base64.urlsafe_b64encode(clock.encode()).decode()
        modes = self.request('/keyinfo', {'spub': self.material['public_key'], 'rand': self.material['nonce'],
            'datetime': encoded, 'regionaldatetime': encoded,
            'timezoneid': base64.urlsafe_b64encode(self.timezone.encode()).decode()}, require_encrypted=True).get('otmSupportFeatures', [])
        if 1 in modes: ownership = {'otmSupportFeature': 1, 'sn': identity['serial']}
        elif 0 in modes: ownership = {'otmSupportFeature': 0}
        else: raise ValueError('This device requires an unsupported ownership confirmation method')
        if self.request('/confirminfo', ownership, encrypted=True): raise ValueError('Ownership was not confirmed')
        return info

    def configure(self, wifi, broker_url, label, before_write=lambda: None):
        rows = self.request('/wifiscaninfo', require_encrypted=True).get('wifiScanInfo', [])
        candidates = [row for row in rows if row.get('ssid') == wifi['ssid'] and row.get('authType') == 3]
        if not candidates: raise ValueError('The target WPA2 network is not visible to the appliance')
        chosen = max(candidates, key=lambda row: row.get('rssi', -999))
        before_write()
        result = self.request('/wifiprovisioninginfo', {'wifiCredential': {
            'ssid': wifi['ssid'], 'password': wifi['password'], 'macAddress': chosen['bssid'], 'authType': 3},
            'brokerUrl': broker_url, 'deviceName': label}, encrypted=True)
        if not isinstance(result.get('lookupId'), str): raise ValueError('Appliance did not confirm Wi-Fi setup')
        if self.request('/setupcomplete', {}, encrypted=True): raise ValueError('Setup completion was not confirmed')


def pair(workspace, config, device, interface=None):
    workspace = Path(workspace)
    material = json.loads((workspace / 'setup-material' / (device['id'] + '.json')).read_text())
    if material['identity'] != device['identity']: raise ValueError('Setup keys do not belong to this selected device')
    verify_certificate(config)
    broker_url = verify_route(config)
    wifi = config['wifi']
    if not isinstance(wifi.get('ssid'), str) or not wifi['ssid'] or not isinstance(wifi.get('password'), str):
        raise ValueError('Supply your own target Wi-Fi credentials first')
    networks = scan(device['identity'])
    if not networks: raise ValueError('No matching appliance AP is visible; enable its documented pairing mode')
    result = {'device': device['id'], 'identity_verified': False, 'write_started': False,
              'configured': False, 'network_restored': False}
    network = APSession(networks[0], interface)
    try:
        with network:
            session = SetupSession(network.gateway, material, config.get('timezone', 'UTC'))
            session.verify_owner(device['identity']); result['identity_verified'] = True
            def before_write():
                result['write_started'] = True; result['configured'] = None
                atomic_json(workspace / ('pairing-' + device['id'] + '.json'), result)
            session.configure(wifi, broker_url, device['label'], before_write); result['configured'] = True
    finally:
        result['network_restored'] = network.restored
        atomic_json(workspace / ('pairing-' + device['id'] + '.json'), result)
    if not network.restored: raise ValueError('Automatic restoration failed; the independent restore timer remains active')
    return result
