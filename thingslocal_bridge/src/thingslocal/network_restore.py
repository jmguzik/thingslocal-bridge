"""Independent NetworkManager restoration helper used by the pairing timer."""
import argparse
import subprocess


def restore(interface, original, temporary):
    def nm(*args):
        return subprocess.run(['nmcli', '--wait', '20', *args], capture_output=True, text=True, timeout=25)
    if original:
        for _ in range(2):
            nm('connection', 'up', 'uuid', original, 'ifname', interface)
            active = nm('-g', 'GENERAL.CON-UUID', 'device', 'show', interface).stdout.strip()
            if active == original:
                nm('connection', 'delete', 'uuid', temporary); return True
        return False
    nm('connection', 'down', 'uuid', temporary)
    nm('connection', 'delete', 'uuid', temporary)
    return nm('-g', 'GENERAL.CON-UUID', 'device', 'show', interface).stdout.strip() != temporary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('interface'); parser.add_argument('original'); parser.add_argument('temporary')
    args = parser.parse_args()
    raise SystemExit(0 if restore(args.interface, args.original, args.temporary) else 1)
