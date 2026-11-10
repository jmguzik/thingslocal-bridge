# Explicit configuration and read-only attribute mappings

The private workspace contains `config.json`. `init` and `add-device` generate it;
no owner-specific address, domain, SSID, model or serial is compiled into the app.

Several appliances share the same `broker` hostname/certificate and MQTT broker.
Each entry in `devices` has a unique `id`, reserved `ip`, registration `label` and
own identity (`manufacturer`, `setup_id`, `serial`, `model`). IDs are not secrets;
identities and configuration files remain private.

The supported built-in profiles are `cooktop`, `dishwasher`, and `generic`.
`generic` uses explicit standard read-only mappings (for example switch state,
temperature, humidity, battery, contact, power and energy where scalar and supplied
with appropriate units). It does not publish all arbitrary incoming attributes.

For an additional scalar attribute, opt in to the exact path:

```json
{
  "entities": [
    {
      "component": "main",
      "capability": "vendor.exampleStatus",
      "attribute": "status",
      "platform": "sensor",
      "name": "Custom status"
    }
  ]
}
```

Supported custom-mapping platforms are `sensor` and `binary_sensor` only. Custom
attribute mappings cannot create command topics. Binary mappings accept boolean values or known on/off,
open/closed and locked/unlocked strings; unknown values become unavailable.
Complex objects/arrays are not automatically converted into scalar readings.

Optional keys include `unit_of_measurement`, `device_class` and `entity_category`.
These must match the actual attribute and Home Assistant's entity schema. The
private catalog records observed paths/types/units without copying values.

To hide virtual or unverified zone components by default, set e.g.:

```json
{"optional_components": ["burner-05", "burner-06"]}
```

This is an installation choice, not a universal assumption about every cooktop.
Keep the installation ID stable for ordinary updates. Use a new ID/workspace for
a deliberately separate clean test, so old discovery entities cannot impersonate
newly reported data.

## Explicit dishwasher program control

Only the dishwasher profile can opt in to the following device-level setting:

```json
{"controls": {"dishwasher_course": true}}
```

The flag must be a JSON boolean. Unknown control names and enabling this control
for a cooktop/generic profile are rejected. The flag is included in private runtime
exports. New and existing installations remain read-only when it is absent/false.

This adds a standard Home Assistant MQTT select with live `supportedCourses`
options, a reported state and an independent readiness/availability topic. The
selector sends only `setWashingCourse`, with one explicitly reported program as
its argument. No arbitrary command or topic can be configured through this flag.
See the README for readiness checks, confirmation and unknown-outcome handling.
