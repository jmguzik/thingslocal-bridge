# Recovery after onboarding or an upgrade

Back up the working app's configuration, certificate/key and registry before
switching endpoints. Keep the previous image installed and stopped during a test.
Only one endpoint may own the same listening port at a time.

## Why an old registry alone may be insufficient

A newly onboarded appliance may receive a different MQTT device ID. Its next login
uses that ID. Restoring an older registry without that ID can reject the appliance,
even if the old certificate, network and serial are unchanged.

## Returning a tested cooktop to the preceding single-device runtime

The old and new implementations use the same unmodified upstream registration
format. A verified new registration can be exported for the previous runtime:

```sh
thingslocal --workspace YOUR_SETUP_WORKSPACE export-legacy-registry DEVICE_ID \
  --state-dir COPIED_NEW_RUNTIME_STATE \
  --legacy-label YOUR_PREVIOUS_REGISTRATION_LABEL \
  --output PRIVATE_OUTPUT_FILE
```

The exporter validates the new registry against the selected installation and
identity, selects exactly one appliance, preserves its current device ID and
changes only the broker-side label. It refuses missing or foreign registrations.
It does not overwrite a running installation or issue any appliance command.

Stop the new endpoint, back up the previous runtime's current registry, copy the
validated output to its expected registry file, then start the previous endpoint.
This requires the same appliance serial/manufacturer, compatible upstream format,
and the same reachable broker hostname/port/certificate. Verify live values after
reconnection. Do not copy another appliance into the old single-device registry.

If no verified new registration exists, do not invent a replacement ID. Restore
the previous endpoint and use a new ownership-verified AP onboarding with its
previous registration label. The pairing tool and setup material can target that
endpoint; full AP identity checks still apply.

For future versions, registry migration must be reviewed against the actual format.
A backup is a recovery input, not proof that recovery has been tested.
