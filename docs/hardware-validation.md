# Hardware validation

Recorded on 2026-11-10. These results apply to the tested hardware and firmware,
not every device sold under a SmartThings label.

## Clean dishwasher installation

The test used a new Python environment, setup workspace, installation ID and
Home Assistant MQTT discovery namespace. The runtime started with no appliance
registrations. The Samsung account was authenticated through a fresh official
CLI browser login, and new EasySetup keys were requested and checked against
the full serial hash. No previous Samsung token, setup-key cache or registration
was imported. The appliance identity came from its own QR label.

The existing owner's domain, RapidSSL certificate, local DNS, Wi-Fi network and
Home Assistant MQTT infrastructure were reused. This was not a test of obtaining
a new domain or issuing a new certificate.

The dishwasher reports model `DW8500-AM`. The generic setup tool verified its
SoftAP identity, completed encrypted EasySetup and restored the setup computer's
home Wi-Fi connection. The appliance then registered with the local endpoint,
completed TLS and sent live MQTT events.

Home Assistant received ten read-only telemetry entities: power, detailed job
state, washing course, operating state, operation time, remaining time, remaining
time display, progress, time until start and expected completion. A separate
certificate-expiry diagnostic was also discovered. Initial reported values
included `ready`, `eco` and `3:50`; these are device reports, not an assertion
that a washing cycle was running. No start, stop or program command was sent.

After restarting the Home Assistant app, the dishwasher reconnected automatically,
completed TLS again and supplied fresh events and all ten telemetry values.
The runtime registration file's SHA-256 stayed unchanged. No AP mode or repeated
pairing was needed for this restart.

Two initial attempts stopped before credential provisioning and restored Wi-Fi.
They exposed a NetworkManager difference: with `ipv4.never-default` enabled,
`IP4.GATEWAY` can be empty and `DHCP4.OPTION` can contain pipe-separated options.
The tool now reads the actual DHCP router and validates it within the AP subnet.
It does not guess a fixed gateway address. Regression tests cover that format.

## Clean cooktop onboarding and combined operation

The Samsung `NZ64B5046KK`, STDK `1.7.4`, previously completed local TLS/MQTT
monitoring and restart tests using the preceding single-device implementation.
The new setup workspace obtained fresh keys for this cooktop. The same generic
setup tool then completed ownership-verified SoftAP onboarding and restored the
computer's home Wi-Fi. No previous cooktop registration was imported.

Both appliances registered independently and simultaneously sent live events to
the same local TLS/MQTT endpoint. Home Assistant exposed distinct device entries
and discovery namespaces. The cooktop supplied twenty enabled telemetry entities:
power, operating state, derived heating state, panel lock, plus mode, level,
residual heat and remaining timer for each of four reported zones. A separate
certificate-expiry diagnostic was also discovered. Additional reported components
were configured as optional and disabled by default.

Initially the cooktop reported power off, `ready`, heating off and zero zone
levels; the dishwasher reported power on and `eco`. Later, local user interaction
on the cooktop produced power on, `run`, heating on, zone `burner-02` at level 11
and zone `burner-04` at level 9. These different simultaneous reports reached
their respective HA devices. No appliance control command was sent by the bridge.
This test did not exercise a complete washing cycle or every cooking mode.

The final app source was rebuilt on Home Assistant. Both appliances automatically
reconnected with fresh TLS/MQTT sessions and events. The combined registration
file's SHA-256 stayed unchanged. A subsequent Mosquitto restart left both
appliance sessions connected to the endpoint; their connection counters did not
reset or increase. All 32 enabled HA entities (30 telemetry values and two
certificate diagnostics) were available after MQTT recovered.

Autostart and watchdog were enabled for the new app. The previous single-device
app was retained for recovery, with autostart and watchdog disabled. No whole-HA
reboot or month-long soak test was performed. No temporary setup AP connection
profiles remained on the setup computer after the tests.

## Separate dishwasher write experiment

After explicit owner authorization, a separate one-shot operator probe sent
`main / samsungce.dishwasherWashingCourse / setWashingCourse` to the dishwasher
over its existing local MQTT connection. The dishwasher was powered on and idle.
The selected course changed from `eco` to `auto`, then was restored to `eco`.
Both transitions were confirmed by fresh device events, and the owner also
observed the change on the appliance. No cycle-start, stop or power command was
sent. The appliance remained in `ready` with job state `none`.

The probe used a temporary private app entrypoint, an exclusive durable one-shot
marker and a journal before each command. An unconfirmed write was not treated
as success. Recovery after a hard process interruption would have been best-effort,
with the journal retained and no automatic retry. No such interruption occurred.

This establishes local course-selection writes on the tested dishwasher. It does
not establish start/pause/cancel support or every setting. At that stage the bridge
exposed read-only entities. The private experimental entrypoint is not distributed;
version 0.2.0 implements the confirmed course setter through an explicit opt-in
Home Assistant select, as recorded below.

## Version 0.2.0 Home Assistant controls and notification routing

The released app was updated with the dishwasher's explicit program-selection
flag enabled. Its original registration file stayed unchanged. Both appliances
reconnected and the cooktop retained its existing read-only telemetry.

The standard HA `select.select_option` service was used for `eco → auto → eco`.
The new select and result diagnostic confirmed both changes from appliance
reports. The supported option list contained eight device-reported programs.
No washing cycle was started.

The included notification blueprint was installed for the HA panel and two
owner-authorized mobile-app notify services. A separate temporary MQTT fixture
sent a clearly marked operator test without changing real appliance states.
The HA automation trace showed the panel action, both mobile notify actions and
the retained acknowledgement, in that order, with successful completion. This
verifies HA service acceptance, not physical delivery on a handset.

Running the same fixture automation again with the acknowledged UUID stopped at
its internal deduplication condition and made no notification service calls.
The test automation, discovery/state/ACK topics and test panel card were removed.

Real appliance faults were not induced. The dishwasher currently reports an empty
error/alarm list and a cleaning threshold of 20. It has not yet supplied a fresh
local cleaning counter, so the derived cleaning sensor stays unavailable. Parser,
queue, occurrence replay, clearing/rearming and threshold behavior were checked
with synthetic unit fixtures; that is distinct from observing a real fault or
cleaning reminder from this unit.
