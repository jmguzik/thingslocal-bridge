# Setup and operation

ThingsLocal Bridge continues **LocalThings by
[purplesym (Lars)](https://codeberg.org/purplesym)** and the LocalThings contributors.
The original MQTT implementation is included unmodified. See
[upstream](https://codeberg.org/purplesym/localthings) and
[attribution](https://github.com/jmguzik/thingslocal-bridge/blob/main/UPSTREAM.md).

Follow the [first-installation guide](https://github.com/jmguzik/thingslocal-bridge#first-installation)
before pairing any appliance. Each owner needs their own domain, compatible TLS
certificate, local DNS override, verified appliance identities and reserved IPs.
RapidSSL through DigiCert Global Root G2 was tested. The endpoint can remain
entirely on your local network.

Set `workspace` to your private exported runtime directory (default:
`/share/thingslocal`). It contains the runtime `config.json`, `server.crt`,
`server.key` and the app-managed `state/` directory. Keep setup-computer Wi-Fi
credentials, Samsung login and EasySetup keys out of the app source.

Start the app, enable autostart/watchdog and pair each appliance using the setup
CLI. Home Assistant's standard MQTT integration creates the reported entities.
Only this endpoint may listen on host port 8883; disable Mosquitto's host-side
8883 mapping while keeping its internal MQTT service available.

Devices are read-only by default. A dishwasher can explicitly enable
`"controls": {"dishwasher_course": true}` in its device configuration. The
**Washing program** select is available only while fresh reports show the appliance
powered on and idle. Selecting a program does not start a wash. Missing values
remain unavailable; a write timeout has an unknown outcome and is not retried.

For panel/phone notifications, install the
[notification blueprint](https://github.com/jmguzik/thingslocal-bridge/blob/main/blueprints/dishwasher_notifications.yaml)
and follow its [first-use acknowledgement setup](https://github.com/jmguzik/thingslocal-bridge/blob/main/docs/notifications.md).
Cleaning reminders need both a fresh cycle count and a positive threshold.

Preserve the full private workspace, especially registration and notification
state, on updates. Renew your certificate manually before expiry and restart the
app after replacing its certificate/key. For recovery, follow
[the recovery guide](https://github.com/jmguzik/thingslocal-bridge/blob/main/docs/recovery.md).
