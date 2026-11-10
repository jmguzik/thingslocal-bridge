"""First-installation workflow for an owner-provided network and appliance."""
import argparse
import asyncio
import getpass
from importlib.resources import files
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

from . import __version__
from . import cloud
from .settings import atomic_json, read_config, runtime_config, validate


def prompt(label, default=None, secret=False):
    text = label + (f' [{default}]' if default is not None else '') + ': '
    value = getpass.getpass(text) if secret else input(text)
    return value if value else default


def selected(config, identifier):
    matches = [item for item in config['devices'] if item['id'] == identifier]
    if len(matches) != 1: raise ValueError('Select a configured device ID')
    return matches[0]


def initialize(workspace):
    workspace.mkdir(mode=0o700, parents=True, exist_ok=True); workspace.chmod(0o700)
    if (workspace / 'config.json').exists(): raise ValueError('Workspace already exists; use another path for a clean test')
    config = {
        'installation_id': secrets.token_hex(6),
        'broker': {'hostname': prompt('Your broker domain name'), 'lan_ip': prompt('Reserved endpoint LAN IP'),
                   'listen_host': '0.0.0.0', 'port': 8883,
                   'certificate': str(Path(prompt('Server certificate chain file')).expanduser().resolve()),
                   'private_key': str(Path(prompt('Matching private key file')).expanduser().resolve())},
        'mqtt': {'host': prompt('HA MQTT host', 'core-mosquitto'), 'port': int(prompt('HA MQTT port', '1883')),
                 'username': prompt('HA MQTT username'), 'password': prompt('HA MQTT password', secret=True),
                 'namespace': 'thingslocal'},
        'wifi': {'ssid': prompt('Appliance Wi-Fi SSID'), 'password': prompt('Appliance Wi-Fi password', secret=True)},
        'timezone': prompt('IANA timezone', 'UTC'), 'devices': [],
    }
    validate(config); atomic_json(workspace / 'config.json', config)
    return {'created': True, 'workspace': str(workspace), 'installation_id': config['installation_id']}


def add_device(workspace, config):
    identifier = prompt('Device ID (lowercase, for example cooktop or dishwasher)')
    qr = prompt('Your onboarding QR URL (leave empty for manual identity)', secret=True)
    if qr: identity = cloud.qr_identity(qr)
    else:
        identity = {'manufacturer': prompt('Four-character manufacturer code from QR/AP'),
                    'setup_id': prompt('Three-digit setup ID from QR/AP (not guessed)'),
                    'serial': prompt('Your full device serial number', secret=True)}
    identity['model'] = prompt('Reported model or your model designation')
    device = {'id': identifier, 'name': prompt('HA display name'),
              'ip': prompt('Reserved appliance LAN IP'), 'identity': identity,
              'label': prompt('Unique registration label', identifier),
              'profile': prompt('Readout profile: generic, cooktop, dishwasher', 'generic'),
              'vendor_name': prompt('Display manufacturer', 'Samsung'),
              'optional_components': [], 'entities': []}
    if device['profile'] == 'dishwasher':
        answer = prompt('Enable program selection without starting a wash? (y/N)', 'n').lower()
        if answer not in ('y', 'yes', 'n', 'no'): raise ValueError('Answer yes or no')
        device['controls'] = {'dishwasher_course': answer in ('y', 'yes')}
    config['devices'].append(device); validate(config); atomic_json(workspace / 'config.json', config)
    return {'added': identifier, 'profile': device['profile']}


def login(workspace, install_cli=False, prefix=None):
    prefix = Path(prefix).resolve() if prefix else workspace / 'official-cli'
    if install_cli:
        subprocess.run(['npm', 'install', '--prefix', str(prefix), '--ignore-scripts', '--no-audit', '--no-fund',
                        '@smartthings/cli@1.10.6'], check=True)
    module = prefix / 'node_modules/@smartthings/cli-lib/dist/login-authenticator.js'
    if not module.exists(): raise ValueError('Official CLI is missing. Run login --install-cli first (Node.js/npm required).')
    environment = dict(os.environ, THINGSLOCAL_WORKSPACE=str(workspace), SMARTTHINGS_CLI_PREFIX=str(prefix))
    subprocess.run(['node', str(files('thingslocal').joinpath('data/samsung-login.cjs'))], env=environment, check=True)
    return {'login_completed': True, 'credentials_printed': False}


def export_runtime(workspace, config, output, target_workspace):
    output = Path(output).resolve()
    if output == workspace or workspace in output.parents:
        raise ValueError('Export to a separate private directory, not over the setup workspace')
    if output.exists() and any(output.iterdir()): raise ValueError('Use an empty export directory')
    output.mkdir(mode=0o700, parents=True, exist_ok=True); output.chmod(0o700)
    exported = runtime_config(config)
    exported['broker'] = dict(exported['broker'])
    for option, filename in (('certificate', 'server.crt'), ('private_key', 'server.key')):
        shutil.copyfile(config['broker'][option], output / filename); (output / filename).chmod(0o600)
        exported['broker'][option] = str(Path(target_workspace) / filename)
    if config['broker'].get('trusted_ca'):
        shutil.copyfile(config['broker']['trusted_ca'], output / 'trusted-ca.pem'); (output / 'trusted-ca.pem').chmod(0o600)
        exported['broker']['trusted_ca'] = str(Path(target_workspace) / 'trusted-ca.pem')
    atomic_json(output / 'config.json', exported)
    return {'exported': True, 'target_workspace': target_workspace, 'device_count': len(config['devices']),
            'wifi_and_samsung_credentials_included': False, 'registry_included': False}


def import_material(workspace, device, filename):
    import base64
    value = json.loads(Path(filename).read_text())
    if isinstance(value, list):
        identity, keys = value
        if identity != [device['identity']['manufacturer'], device['identity']['setup_id'], device['identity']['serial']]:
            raise ValueError('Imported material belongs to another device')
        value = {'identity': device['identity'], 'nonce': keys[0], 'public_key': keys[1], 'shared_key': keys[2]}
    if any(value['identity'].get(key) != device['identity'][key] for key in ('manufacturer', 'serial', 'setup_id')):
        raise ValueError('Imported material does not match the selected device')
    for key in ('public_key', 'shared_key'):
        if len(base64.b64decode(value[key], altchars=b'-_', validate=True)) != 32: raise ValueError('Invalid imported key')
    value['identity'] = device['identity']
    atomic_json(workspace / 'setup-material' / (device['id'] + '.json'), value)
    return {'imported': True, 'device': device['id'], 'fresh_cloud_request': False}


def main():
    parser = argparse.ArgumentParser(description='ThingsLocal Bridge: read-only SmartThings STDK MQTT appliances')
    parser.add_argument('--workspace', type=Path, default=Path.home() / '.config/thingslocal')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init'); commands.add_parser('add-device')
    auth = commands.add_parser('login'); auth.add_argument('--install-cli', action='store_true'); auth.add_argument('--cli-prefix')
    listing = commands.add_parser('cloud-devices'); listing.add_argument('--token-file', type=Path)
    keys = commands.add_parser('keys'); keys.add_argument('device'); keys.add_argument('--token-file', type=Path); keys.add_argument('--import-file', type=Path)
    scan = commands.add_parser('scan'); scan.add_argument('device')
    pairing = commands.add_parser('pair'); pairing.add_argument('device'); pairing.add_argument('--interface')
    commands.add_parser('check')
    export = commands.add_parser('export-runtime'); export.add_argument('--output', type=Path, required=True)
    export.add_argument('--target-workspace', default='/share/thingslocal')
    runtime = commands.add_parser('serve'); runtime.add_argument('--config', type=Path, required=True); runtime.add_argument('--state-dir', type=Path, required=True)
    commands.add_parser('app')
    catalog = commands.add_parser('catalog'); catalog.add_argument('device'); catalog.add_argument('--state-dir', type=Path, required=True)
    recovery = commands.add_parser('export-legacy-registry'); recovery.add_argument('device')
    recovery.add_argument('--state-dir', type=Path, required=True); recovery.add_argument('--legacy-label', required=True)
    recovery.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); workspace = args.workspace.expanduser().resolve()
    os.umask(0o077)
    try:
        if args.command == 'init': result = initialize(workspace)
        elif args.command == 'login':
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            result = login(workspace, args.install_cli, args.cli_prefix)
        elif args.command == 'serve':
            from .server import serve
            asyncio.run(serve(args.config, args.state_dir)); return 0
        elif args.command == 'app':
            from .server import serve
            options = json.loads(Path('/data/options.json').read_text()); directory = Path(options['workspace'])
            asyncio.run(serve(directory / 'config.json', directory / 'state')); return 0
        elif args.command == 'cloud-devices': result = cloud.devices(cloud.read_token(workspace, args.token_file))
        else:
            config = read_config(workspace)
            if args.command == 'add-device': result = add_device(workspace, config)
            elif args.command == 'keys':
                device = selected(config, args.device)
                result = (import_material(workspace, device, args.import_file) if args.import_file else
                          cloud.acquire_material(workspace, device, cloud.read_token(workspace, args.token_file)))
            elif args.command == 'scan':
                from .network import scan
                found = scan(selected(config, args.device)['identity'])
                atomic_json(workspace / ('ap-scan-' + args.device + '.json'), found)
                result = {'matching_access_points': len(found), 'private_details_saved': True}
            elif args.command == 'pair':
                from .provision import pair
                result = pair(workspace, config, selected(config, args.device), args.interface)
            elif args.command == 'check':
                from .security import verify_certificate, verify_route
                _, expiry = verify_certificate(config); verify_route(config)
                result = {'certificate_and_private_route_valid': True, 'certificate_expiry': expiry}
            elif args.command == 'export-runtime': result = export_runtime(workspace, config, args.output, args.target_workspace)
            elif args.command == 'catalog':
                selected(config, args.device)
                result = json.loads((args.state_dir / ('catalog-' + args.device + '.json')).read_text())
            elif args.command == 'export-legacy-registry':
                from .recovery import export_legacy_registration
                records = export_legacy_registration(config, args.state_dir, args.device, args.legacy_label)
                atomic_json(args.output, records)
                result = {'exported': True, 'device': args.device, 'new_device_id_preserved': True}
            else: raise ValueError('Unknown command')
        print(json.dumps(result, indent=2)); return 0
    except KeyboardInterrupt:
        print(json.dumps({'cancelled': True}), file=sys.stderr)
        return 130
    except ImportError:
        print(json.dumps({'error': 'Install setup dependencies with pip install -e ".[setup]"'}), file=sys.stderr)
        return 1
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        # Do not print payloads, request headers, private identities or credentials.
        message = str(error) if isinstance(error, ValueError) else 'Operation failed; check prerequisites and private workspace files'
        print(json.dumps({'error': message, 'error_type': type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
