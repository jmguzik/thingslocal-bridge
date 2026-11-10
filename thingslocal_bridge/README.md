# ThingsLocal Bridge

Continued work on **LocalThings by [purplesym (Lars)](https://codeberg.org/purplesym)**
and the LocalThings contributors. The original MQTT implementation is included
unmodified. [Original project](https://codeberg.org/purplesym/localthings) ·
[Protocol research](https://6xq.net/localthings/) · see UPSTREAM.md for attribution.

Local monitoring of multiple compatible SmartThings STDK MQTT appliances.
Experimental; this does not support every device connected to SmartThings.
Devices are read-only by default. A dishwasher can explicitly opt in to program
selection with `"controls": {"dishwasher_course": true}` in its private device
configuration. Home Assistant then provides a standard **Washing program**
dropdown and a result diagnostic. Selecting a program does not start a wash.

You need your own domain and valid TLS certificate for the endpoint hostname.
**RapidSSL with a chain through DigiCert Global Root G2 was tested.** Local DNS
can keep the endpoint entirely inside your home network, without public hosting
or Internet port forwarding. Each appliance needs its own verified identity and
reserved LAN IP. One endpoint/certificate is shared by compatible appliances.

Use the setup computer CLI and the repository's first-installation README to:

1. Create a private workspace with your network, TLS and MQTT settings.
2. Sign into your own Samsung account and obtain each device's EasySetup material.
3. Export a private runtime bundle and copy it into HA's share folder.
4. Configure this app's `workspace` option, start it, and check local DNS/TLS.
5. Pair each appliance in its manufacturer-documented AP mode.

The default runtime workspace is `/share/thingslocal`. It must contain
`config.json`, `server.crt` and `server.key`. The configuration supports multiple
appliances with distinct IDs, identities, IPs and registration labels. All files
are private: do not place them in this app's source or a public repository.

Registration is kept under `workspace/state/`. Preserve that state on ordinary
updates and when adding another appliance. Autostart and watchdog are supported.
Only one endpoint may listen on host port 8883; disable Mosquitto's host-side
8883 port mapping. The HA MQTT broker itself is separate from this endpoint.

The app publishes standard HA MQTT sensor/binary_sensor discovery. Only an
explicitly enabled dishwasher subscribes to its own program-selection command
topic and exposes a select entity. There are no start, pause, cancel, power or
cooktop controls. It reports each attribute's availability
independently. Missing data never becomes zero/off. Unknown attributes are not
automatically published; they require an explicit read-only mapping.

The program selector is available only while the dishwasher is powered on and
idle, has fresh program/options/readiness data, and has a live command connection.
Changes are confirmed from actual device reports. A timeout leaves the outcome
unknown and blocks further requests until new program data or reconnection.
Commands are not retained, queued for reconnect or retried automatically.

Dishwasher profiles also provide reported message and cleaning-reminder sensors.
Cleaning requires fresh local count and threshold readings. The repository's
notification blueprint sends attention reports to the HA panel and your selected
notify services. Follow its first-use acknowledgement setup before enabling it.
Keep `state/notifications-*.json`: it contains deduplication history and the
durable pending-notification queue, not a replacement for fresh appliance data.

Renew TLS manually before expiry, replace the certificate/key and restart.
Certificate expiry is available as a diagnostic entity for each appliance.

The MQTT implementation is from purplesym/localthings. The source is included in
`src/localthings/`; attribution and AGPL-3.0-or-later license are in UPSTREAM.md and
LICENSE. The upstream checks identity claims but does not verify JWT signatures;
keep this endpoint on a trusted private LAN.
