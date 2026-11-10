"""Certificate and local-route checks before sending appliance Wi-Fi credentials."""
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
import socket
import ssl
import subprocess


def verify_certificate(config):
    broker = config['broker']; cert = Path(broker['certificate']); key = Path(broker['private_key'])
    root = broker.get('trusted_ca') or str(files('thingslocal').joinpath('data/DigiCertGlobalRootG2.pem'))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    subprocess.run(['openssl', 'verify', '-no-CApath', '-no-CAstore', '-CAfile', root,
                    '-purpose', 'sslserver', '-verify_hostname', broker['hostname'],
                    '-untrusted', str(cert), str(cert)], check=True, capture_output=True)
    result = subprocess.run(['openssl', 'x509', '-in', str(cert), '-noout', '-enddate'],
                            check=True, capture_output=True, text=True)
    expiry = datetime.strptime(result.stdout.strip().split('=', 1)[1],
                               '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
    if expiry <= datetime.now(timezone.utc): raise ValueError('The broker certificate has expired')
    return context, expiry.isoformat()


def verify_route(config):
    broker = config['broker']; port = broker.get('port', 8883)
    addresses = {item[4][0] for item in socket.getaddrinfo(broker['hostname'], port, socket.AF_UNSPEC, socket.SOCK_STREAM)}
    if addresses != {broker['lan_ip']}:
        raise ValueError('Broker DNS must resolve exactly to the configured private LAN address')
    with socket.create_connection((broker['lan_ip'], port), timeout=3): pass
    return 'ssl://' + broker['hostname'] + ':' + str(port)
