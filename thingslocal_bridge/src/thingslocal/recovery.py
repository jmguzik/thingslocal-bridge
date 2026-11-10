"""Explicit, validated recovery export for the previous single-cooktop runtime."""
from .settings import SLUG, load_registry


def export_legacy_registration(config, state_directory, device_id, legacy_label):
    from pathlib import Path
    if not SLUG.fullmatch(legacy_label): raise ValueError('Invalid legacy registration label')
    selected = [device for device in config['devices'] if device['id'] == device_id]
    if len(selected) != 1: raise ValueError('Select one configured appliance')
    serial = selected[0]['identity']['serial']
    matches = [dict(row) for row in load_registry(Path(state_directory)/'registrations.json',config)
               if row['serialNumber'] == serial]
    if len(matches) != 1: raise ValueError('No verified new registration exists for this selected appliance')
    matches[0]['name'] = legacy_label
    return matches
