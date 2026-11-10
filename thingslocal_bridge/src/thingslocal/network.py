"""Linux NetworkManager SoftAP connection with an independent restoration timer."""
import re
import ipaddress
import secrets
import subprocess
import sys
from .cloud import matching_ap
from .network_restore import restore


def command(*args, check=True, timeout=30):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=timeout)


def nm(*args, check=True): return command('nmcli', '--wait', '20', *args, check=check)


def ap_gateway(interface):
    # never-default deliberately removes IP4.GATEWAY. DHCP retains the router
    # option, so it is the authoritative fallback rather than a guessed .1 IP.
    value = nm('-g', 'IP4.GATEWAY', 'device', 'show', interface).stdout.strip()
    candidates = [] if value in ('', '--') else value.splitlines()
    if not candidates:
        options = nm('-g', 'DHCP4.OPTION', 'device', 'show', interface).stdout
        for line in re.split(r'\s*\|\s*|\n', options):
            match = re.fullmatch(r'routers\s*=\s*(.+)', line.strip())
            if match: candidates.extend(re.split(r'[,\s]+', match[1]))
    addresses = nm('-g', 'IP4.ADDRESS', 'device', 'show', interface).stdout.splitlines()
    local = [ipaddress.ip_interface(item) for item in addresses if item]
    networks = [item.network for item in local]
    own_addresses = {item.ip for item in local}
    valid = set()
    for candidate in candidates:
        try: address = ipaddress.ip_address(candidate)
        except ValueError: continue
        if (address.version == 4 and address.is_private and not address.is_loopback
                and not address.is_link_local and not address.is_unspecified
                and address not in own_addresses and any(
                address in network and address not in (network.network_address, network.broadcast_address)
                for network in networks)): valid.add(str(address))
    if len(valid) != 1: raise ValueError('No unique DHCP router in the appliance AP subnet')
    return valid.pop()


def scan(identity):
    result = nm('-t', '--escape', 'no', '-f', 'SSID,BSSID,SIGNAL', 'device', 'wifi', 'list', '--rescan', 'yes')
    found = []
    for line in result.stdout.splitlines():
        match = re.fullmatch(r'(.*):([A-Fa-f0-9]{2}(?::[A-Fa-f0-9]{2}){5}):(\d+)', line)
        if match and matching_ap(match[1], identity):
            found.append({'ssid': match[1], 'bssid': match[2], 'signal': int(match[3])})
    return sorted(found, key=lambda item: item['signal'], reverse=True)


class APSession:
    def __init__(self, network, interface=None):
        self.network, self.interface = network, interface
        self.original = self.temporary = self.guard = None
        self.restored = False

    def __enter__(self):
        rows = nm('-t', '-f', 'DEVICE,TYPE,STATE', 'device').stdout.splitlines()
        wifi = [row.split(':') for row in rows if ':wifi:' in row]
        if self.interface:
            wifi = [row for row in wifi if row[0] == self.interface]
        else:
            connected = [row for row in wifi if row[2] == 'connected']
            if len(connected) == 1: wifi = connected
        if len(wifi) != 1: raise ValueError('Select exactly one Wi-Fi interface with --interface')
        self.interface = wifi[0][0]
        original = nm('-g', 'GENERAL.CON-UUID', 'device', 'show', self.interface).stdout.strip()
        self.original = original if original and original != '--' else ''
        if not self.original and wifi[0][2].startswith('connected'):
            raise ValueError('Externally managed Wi-Fi without a restorable NetworkManager profile is unsupported')
        if self.original:
            original_ssid = nm('-g', '802-11-wireless.ssid', 'connection', 'show', 'uuid', self.original).stdout.strip()
            if original_ssid == self.network['ssid']: raise ValueError('Start pairing while connected to your home network')
        command('systemctl', '--user', 'is-active', 'default.target')
        label = 'thingslocal-ap-' + secrets.token_hex(4)
        try:
            nm('connection', 'add', 'type', 'wifi', 'ifname', self.interface, 'con-name', label,
               'ssid', self.network['ssid'], '802-11-wireless.bssid', self.network['bssid'],
               '802-11-wireless-security.key-mgmt', 'wpa-psk',
               '802-11-wireless-security.psk', '1111122222',
               'connection.autoconnect', 'no', 'ipv4.never-default', 'yes', 'ipv6.method', 'disabled')
            self.temporary = nm('-g', 'connection.uuid', 'connection', 'show', 'id', label).stdout.strip()
            self.guard = label + '-restore'
            command('systemd-run', '--user', '--quiet', '--unit=' + self.guard, '--on-active=120s',
                    sys.executable, '-m', 'thingslocal.network_restore', self.interface,
                    self.original, self.temporary)
            nm('connection', 'up', 'uuid', self.temporary, 'ifname', self.interface)
            self.gateway = ap_gateway(self.interface)
            return self
        except BaseException:
            self.__exit__(None, None, None); raise

    def __exit__(self, *unused):
        if self.temporary:
            self.restored = restore(self.interface, self.original, self.temporary)
            if self.restored and self.guard:
                command('systemctl', '--user', 'stop', self.guard + '.timer', self.guard + '.service', check=False)
        else: self.restored = True
