# ThingsLocal Bridge

**Continued work on [LocalThings](https://codeberg.org/purplesym/localthings) by
[purplesym (Lars)](https://codeberg.org/purplesym) and the LocalThings contributors.**

The original MQTT implementation and protocol research are the foundation of
this project. Its MQTT source is included unmodified, with the original copyright
notices and AGPL-3.0-or-later license preserved. This continuation adds Home
Assistant packaging, guided setup, multi-appliance monitoring, optional dishwasher
program selection and notification routing. See [UPSTREAM.md](UPSTREAM.md) for
the original sources and the changes made here.

**Local monitoring for compatible SmartThings STDK MQTT appliances, with optional
dishwasher program selection.**

Run one local endpoint for several appliances and expose their reported values
through the standard Home Assistant MQTT integration. Device identities,
registration, state sessions and discovery namespaces remain separate.
Appliances are read-only by default. Program selection is an explicit per-device
opt-in for dishwashers; it does not start a wash.

This is an experimental community project, unaffiliated with Samsung or
SmartThings. It uses [purplesym's LocalThings](https://codeberg.org/purplesym/localthings)
MQTT implementation and [documented EasySetup protocol](https://6xq.net/localthings/).
See [UPSTREAM.md](UPSTREAM.md) and LICENSE for source and attribution.

## Scope and compatibility

The supported path is **STDK MQTT with SoftAP EasySetup 1.x**. A SmartThings label
or the term "Direct Connected" alone does not establish compatibility. This
project does not implement OCF/CoAP, Zigbee, Z-Wave or Bluetooth onboarding.

| Appliance | Evidence |
| --- | --- |
| Samsung NZ64B5046KK, STDK 1.7.4 | Fresh cloud-key acquisition, ownership-verified SoftAP onboarding, local TLS/MQTT registration and twenty read-only telemetry entities verified in the new multi-device installation. |
| Samsung dishwasher reporting DW8500-AM | Fresh Samsung login/key acquisition, ownership-verified SoftAP onboarding, local TLS/MQTT registration and ten read-only telemetry entities verified with an empty runtime registry and new HA namespace. |
| Other models | Unverified; matching the protocol and supplying a supported profile/explicit mappings are required. |

A cooktop and dishwasher profile are included. The generic profile exposes a
small explicit list of standard read-only attributes. Additional scalar
attributes require an explicit allowlist in the device configuration. Unknown
attributes are catalogued by **name/type/unit only**, without their values.

## What you must supply

- **Your own registered domain name** and control of its DNS records.
- **Your own valid TLS certificate for the broker hostname**, matching private key
  and intermediate chain. **RapidSSL was tested successfully**, using
  `RapidSSL TLS RSA CA G1 → DigiCert Global Root G2`, with an RSA 2048-bit key.
  A self-signed certificate was rejected by the tested cooktop. Other issuers,
  roots and key types have not been established as compatible.
- A local DNS override from that name to your endpoint's reserved LAN IP.
  **The endpoint can remain entirely local**: public hosting and Internet port
  forwarding are not required. One hostname/certificate serves all appliances
  using this endpoint and compatible with its certificate chain.
- Your appliances' own SmartThings QR data, or verified manual manufacturer,
  setup ID and serial. **There is no default or guessed setup ID.**
- A Samsung account for initial EasySetup material and a WPA2 Wi-Fi network
  visible to the appliance. Setup/cloud credentials stay on the setup computer;
  routine monitoring does not call the SmartThings API.
- A Linux setup computer with NetworkManager, a Wi-Fi adapter, a systemd user
  session, Python 3.11+, OpenSSL 3, and Node.js/npm for the optional official login.
- Home Assistant OS, access to its local-apps/share folders, and a working HA MQTT
  integration/broker. The official Mosquitto app is a suitable choice.
- DHCP reservations for the endpoint and **every** appliance.

Certificate renewal is manual. Renew before expiry, replace the private files
and restart the app. Expiry is exposed as a diagnostic HA entity.

## First installation

Start from a checkout of this repository:

```sh
git clone https://github.com/jmguzik/thingslocal-bridge.git
cd thingslocal-bridge
```

All commands below run on the Linux
setup computer unless stated otherwise. Replace example paths with your own.

### 1. Install the setup CLI into a new environment

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[setup]'
.venv/bin/thingslocal --workspace ~/.config/thingslocal init
```

The wizard asks for your domain, reserved endpoint IP, certificate/key paths,
HA MQTT credentials, target Wi-Fi credentials and timezone. Passwords are entered
with a hidden prompt. Private files use mode 0600 inside a mode-0700 workspace.
Do not put the workspace or an exported private runtime bundle in a public repo.

### 2. Sign into your own Samsung account and add appliances

```sh
.venv/bin/thingslocal --workspace ~/.config/thingslocal login --install-cli
.venv/bin/thingslocal --workspace ~/.config/thingslocal cloud-devices
.venv/bin/thingslocal --workspace ~/.config/thingslocal add-device
.venv/bin/thingslocal --workspace ~/.config/thingslocal keys YOUR_DEVICE_ID
```

`login --install-cli` downloads the pinned official SmartThings CLI login support.
Authentication opens Samsung's browser flow. If the browser does not appear,
open the printed `http://localhost:PORT/start` link **on the same computer**.
The callback is bound to loopback. Credentials are saved privately and not printed.
A generic HA integration/PAT token may lack the Samsung identity needed by
EasySetup; use the official Samsung user login when the API reports this problem.

`add-device` accepts the URL from your own QR or explicit manual identity. Choose
a unique ID, reserved IP, display name, registration label and profile (`cooktop`,
`dishwasher`, or `generic`). Repeat `add-device` and `keys` for additional appliances.
Changing a display name later does not require changing the identity or label.

`keys` makes a **new** cloud-assisted key request and verifies the complete serial
hash in the response. Keep the resulting `setup-material` files private. To use
material you already own, `keys YOUR_DEVICE_ID --import-file FILE` is available,
but a test using this option is **not** a fresh cloud-key acquisition test.

### 3. Prepare Home Assistant and the private runtime bundle

Install/configure your regular MQTT broker and the standard MQTT integration.
Create a dedicated broker login for this bridge. The bridge needs publication
access to its `thingslocal/...` state topics and `homeassistant/.../config`
discovery topics, plus subscription access to `homeassistant/status`. Dishwashers
also subscribe to their own `thingslocal/DEVICE_IDENTIFIER/notification_ack`
topic. An enabled program selector subscribes to its own
`thingslocal/DEVICE_IDENTIFIER/command/washing_program` topic.

The appliance endpoint occupies host TCP port **8883**. Disable Mosquitto's
host-side 8883 mapping to avoid a conflict. When both clients run inside HA,
Mosquitto's host-side ports can all be disabled and `core-mosquitto:1883` used
through the internal app network.

```sh
.venv/bin/thingslocal --workspace ~/.config/thingslocal export-runtime \
  --output /path/to/empty-private-export \
  --target-workspace /share/thingslocal
```

This exports only runtime configuration, your certificate and private key.
**It does not copy Wi-Fi credentials, Samsung tokens, EasySetup keys or an old
registration.** The target path must match the app's `workspace` option.

Copy the exported files into HA's `share/thingslocal` directory, preserving
private access. Do not put them in `www`, the local app source or a Git checkout.

### 4. Install the Home Assistant app

Add this repository under **Settings → Apps → Install app → Repositories**:

```text
https://github.com/jmguzik/thingslocal-bridge
```

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fjmguzik%2Fthingslocal-bridge)

Install **ThingsLocal Bridge** from that repository. Set its `workspace` option to
the private runtime directory prepared above, then start it and enable
start-on-boot/watchdog. This app is installed through HA's app store; HACS is not
required.

For a local development installation instead:

Copy **only** this repository's `thingslocal_bridge/` directory into HA's
`local_apps/thingslocal_bridge` folder. Current HA exposes this through Samba or
as `/local_apps` in the Terminal & SSH app. Older releases may call it `addons`.
See the [official local-app tutorial](https://contributors.home-assistant.io/apps/first-app/).

In HA, open **Settings → Apps → Install app → Check for updates**. Under Local
apps, install **ThingsLocal Bridge**. Set its `workspace` option to the private
runtime directory, then start it and enable start-on-boot/watchdog.

The app can run before appliance onboarding, with an empty registry. Its log
should report `thingslocal_ready`. It creates certificate diagnostics in HA;
other entities appear as their whitelisted attributes are reported.

### 5. Set up local DNS and check routing before pairing

Configure your local DNS so your broker hostname resolves **only** to the
reserved private endpoint IP, and advertise that DNS through DHCP. Reserve each
appliance IP used by the configuration.

```sh
.venv/bin/thingslocal --workspace ~/.config/thingslocal check
```

The check validates hostname, trusted certificate chain, key match, expiry, exact
private DNS resolution and endpoint TCP reachability. It also rejects any
additional IPv6/AAAA address: this endpoint is IPv4-only. Certificate validation
uses DigiCert Global Root G2; other trust bundles require explicit configuration
and separate device verification.

### 6. Put one appliance into its documented AP mode and pair it

Use the manufacturer's procedure for the **particular model**. Button sequences
are not universal. Keep the computer connected to its normal home network first.

```sh
.venv/bin/thingslocal --workspace ~/.config/thingslocal scan YOUR_DEVICE_ID
.venv/bin/thingslocal --workspace ~/.config/thingslocal pair YOUR_DEVICE_ID
```

Select a Wi-Fi adapter with `pair YOUR_DEVICE_ID --interface INTERFACE` if needed.
The tool verifies the AP's identity and complete serial hash before sending
Wi-Fi credentials. It performs encrypted EasySetup, restores the computer's
original connection and removes its temporary AP profile. An independent
120-second systemd user timer also attempts restoration if the process crashes.
A timeout after a configuration write is reported as **unknown**, not unchanged.

Onboarding points the appliance at your local endpoint instead of its previous
broker. Its cloud-app monitoring may consequently stop. By default the project
exposes only readouts. The optional dishwasher program selector is described below.

Repeat pairing for each configured appliance. Use an existing workspace to add
devices: export the updated configuration into a new empty export directory,
copy those files over the runtime configuration, **preserve the HA `state/`
folder**, restart the app and then pair the added appliance.

## Readouts and availability

- Each configured appliance has its own HA device, registration and state session.
- The cooktop profile includes reported power, operating/heating state, zone
  settings/modes, timers, residual heat and panel lock.
- The dishwasher profile includes reported power, machine/job states, program,
  progress, remaining time and completion timestamp where actually supplied.
- Values are settings reported by the appliance, not proof of electrical power
  consumption or physical heat delivery.
- A new connection clears previous field values. Each entity remains unavailable
  until its own fresh value arrives; missing data never becomes zero/off.
- Components listed in a device's `optional_components` are disabled by default
  in HA. Do not assume burner IDs correspond to physical positions.
- Unknown attributes appear in a private metadata catalog. `catalog DEVICE_ID
  --state-dir DIR` prints its names/types/units, not raw values. Additional scalar
  mappings are described in [configuration.md](docs/configuration.md).

The endpoint keeps a durable registry and bounded rotating logs. HA MQTT outages
are retried independently; snapshots are coalesced instead of queued indefinitely.
The upstream protocol checks source IP and JWT identity claims but does not
verify appliance JWT signatures. Keep the endpoint on a trusted private LAN.

## Optional dishwasher program selection

For a dishwasher profile, `add-device` asks whether to enable program selection;
the default answer is **no**. For an existing dishwasher, add this to its private
device configuration, then deploy the updated runtime configuration and restart
the app while preserving its `state/` directory:

```json
{"controls": {"dishwasher_course": true}}
```

Home Assistant adds a **Washing program** dropdown and a **Program selection
result** diagnostic to that appliance. The dropdown uses only the supported
programs reported by the device. It works through the standard MQTT select UI
on a computer or phone; no custom card is needed. Existing telemetry entities
keep their identifiers.

Selection is available only with fresh session data showing power on, operating
state `ready`, job state `none`, a known selected program and a live command
subscription. It becomes unavailable during a pending change, while busy/off,
or after disconnect. Normal telemetry remains available independently.

The only permitted command is `samsungce.dishwasherWashingCourse.setWashingCourse`.
**It selects a program without starting a wash.** There are no start, pause,
cancel, power or cooktop controls. The displayed selection follows device reports,
not a predicted result. A request waits up to ten seconds for a fresh matching
report. A timeout means **outcome unknown**; the selector then waits for another
program report or a fresh connection before accepting more requests.

Command messages use QoS 0, clean MQTT sessions and `retain: false`. Retained
replays are rejected, and commands are not retried or saved for reconnection.
Only one request per appliance can be pending. Check the diagnostic if a request
is rejected or not confirmed. Do not automatically repeat unknown-outcome requests.

Set the flag to `false` or remove it and restart to return the device to read-only
operation. Its command subscription and selector discovery are then removed.

## Dishwasher attention and cleaning notifications

Dishwasher profiles expose reported device messages, a reported-attention flag,
the cleaning count/threshold and a derived cleaning recommendation. Missing data
remains unavailable. A recommendation needs both a fresh local cycle count and
a positive threshold. A threshold alone does not establish whether cleaning is due.

The included [notification blueprint](blueprints/dishwasher_notifications.yaml)
routes messages to the HA notification panel and any explicitly configured phone
notify services. It does not require a Samsung cloud API connection. Installation,
first-use acknowledgement initialization and delivery limits are described in
[notifications.md](docs/notifications.md).

## Clean testing and recovery

A clean test uses a new setup workspace, a new installation ID, **empty** runtime
state/registry and a distinct MQTT namespace/HA unique IDs. It must not adopt old
registrations or use old retained values as evidence. Reusing owned domain/TLS
and network infrastructure is allowed and must be stated separately.

Fresh cloud-key acquisition and explicit key import are different test paths.
Do not describe an imported cached key as newly obtained from Samsung.

Before changing a working installation, back it up. After a new onboarding, the
appliance may know a **new device ID**: restoring only the old registry is not a
complete rollback. See [recovery.md](docs/recovery.md) for validated registry
migration or a new ownership-verified onboarding to the previous endpoint.

## Tests and publishing

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Unit tests cover different appliances/networks, identity and AP checks, multi-device
registration/IP isolation, read-only discovery, state invalidation, private export
and recovery. They also cover opt-in program controls, connection generations,
retained replay rejection, fresh confirmations and the durable notification queue.
Hardware verification is recorded separately; synthetic tests do
not establish compatibility with every SmartThings device.

See [hardware-validation.md](docs/hardware-validation.md) for the tested scope and
remaining checks.

Before publishing, review the complete tracked tree and history. Exclude all
private workspaces, tokens, setup keys, runtime registry and user private keys.
The vendored MQTT source and attribution must remain available under its license.
