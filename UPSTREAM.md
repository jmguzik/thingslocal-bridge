# Upstream source and attribution

**ThingsLocal Bridge is continued work on LocalThings, originally authored by
[purplesym (Lars)](https://codeberg.org/purplesym), with contributions from the
LocalThings community.** The original protocol research and MQTT implementation
are the foundation of this continuation. The original MQTT source is included
unmodified, with its copyright notices and license preserved.

- Source: https://codeberg.org/purplesym/localthings
- Protocol/EasySetup documentation: https://6xq.net/localthings/
- License: GNU Affero General Public License, version 3 or later.
- Unmodified source included in `thingslocal_bridge/src/localthings/`.
- SHA-256 of the imported `server.py`:
  `e204985404a085805a78c400ca60a7256dbab653337f4d105c1edaefbe5f8434`.

The configuration wizard, verified SoftAP setup wrapper, multi-device binding,
durable registry, read-only telemetry profiles, opt-in dishwasher program selection,
HA MQTT discovery, notification queue and blueprint, packaging,
and tests are the additions in this repository. They are AGPL-3.0-or-later;
see LICENSE. The setup wrapper follows the upstream protocol documentation and
preserves its ownership and encryption exchanges.

Other dependencies:

- Eclipse Paho MQTT Python 2.1.0: https://github.com/eclipse-paho/paho.mqtt.python
- structlog 26.1.0: https://github.com/hynek/structlog
- PyCryptodome 3.23.0 (setup tool only): https://www.pycryptodome.org/
- Official SmartThings CLI 1.10.6 login support (downloaded for setup only):
  https://github.com/SmartThingsCommunity/smartthings-cli
- DigiCert Global Root G2 is a public CA certificate included for chain checks.

The official CLI handles the Samsung OAuth/PKCE flow. The wrapper restricts its
local callback listener to loopback and prints the local start URL if automatic
browser opening fails. It does not replace the provider's authentication flow.

Dependency source and license notices installed by pip/npm remain with those
packages. No Samsung tokens, appliance keys, user certificates or user private
keys are part of this source distribution.
