# Dishwasher notifications

The bridge interprets only explicitly supported local dishwasher reports:

- `samsungce.errorAndAlarmState.events`: reported codes and optional descriptions.
  Unknown codes remain neutral device messages, without an invented fault meaning.
  These reports do not prove that a physical fault remains active.
- `samsungce.drumSelfCleaning.washingCountAfterSelfClean` and
  `suggestionThreshold`: cleaning is recommended when both are freshly available,
  the threshold is positive, and the count reaches it. No counter is imported from
  old cloud snapshots. Some devices report the counter only after a completed cycle.

Ordinary `DA_DW_DoorA_Opened` reports are excluded unless the appliance explicitly
marks the entry as an error. `DA_DW_T_0001` and `DA_DW_M_0001` were observed after
cycle completion upstream, but their exact meanings are unverified; they are
forwarded neutrally rather than silently discarded as completion messages.
Rinse-aid and other codes retain their actual code/description. An explicit
device-reported `type: error` is preserved. The metadata
catalog still records supported paths; unknown capabilities are not auto-exposed.

## Install the Home Assistant automation

1. Copy `blueprints/dishwasher_notifications.yaml` into
   `/config/blueprints/automation/thingslocal/dishwasher_notifications.yaml` on HA.
2. Reload automations, then create an automation from **ThingsLocal
   dishwasher notifications**. Select that dishwasher's **Notification event**
   and **Notification acknowledgement** diagnostic sensors.
3. Enter its acknowledgement topic:
   `thingslocal/DEVICE_IDENTIFIER/notification_ack`. The identifier is the same
   as the MQTT device ID and the prefix in its generated entity IDs.
4. For a **new installation only**, initialize that topic once in Developer Tools
   → Actions. If an acknowledgement already exists, preserve it; resetting it can
   cause a pending event to be sent again.

```yaml
action: mqtt.publish
data:
  topic: thingslocal/YOUR_DEVICE_IDENTIFIER/notification_ack
  payload: none
  qos: 1
  retain: true
```

5. Set the title and a unique panel notification ID for this appliance. To add
   phones, enter a YAML list of your actual HA notify services, for example:

```yaml
- notify.mobile_app_your_phone
- notify.mobile_app_second_authorized_phone
```

The HA panel is always included. Save/enable the automation. Messages are posted
only for real pending device reports; installation does not create a fake fault.
The generated sensors can also be used by your own HA automations.

## Dedupe, queueing and delivery limits

The bridge keeps a private, durable FIFO of up to 64 pending messages. Only its
head is published. HA acknowledges that exact UUID after the panel and all
configured notify service calls succeed; the bridge then advances the queue.
If a notify service fails, the automation stops before acknowledging. Inspect
its trace, correct the service and manually run it again. A successful HA call
means that HA accepted the notification, not that a handset displayed it.

The automation waits for the MQTT acknowledgement sensor to initialize after HA
startup and rechecks UUIDs inside each queued execution. Retained acknowledgements
allow normal HA, broker and bridge restarts without repeatedly sending the same
report. A crash or partial delivery between sending and acknowledgement can still
produce a duplicate when retrying. Exactly-once handset delivery is not promised.

Reported occurrence timestamps distinguish new occurrences from replayed history;
without one, repeated currently reported codes are deduplicated until a clear
report rearms them. History is bounded to 256 occurrence hashes. Cleaning is
rearmed by a fresh count below the threshold. Missing or malformed data does not
clear a previous reminder or become proof that everything is fine.

When the 64-message queue is full, the tail combines new/older pending details
into a bounded summary; its text can be shortened. The in-flight head remains
unchanged until acknowledged. This is a notification mechanism, not a complete
error-history recorder. Preserve `state/notifications-*.json` on app updates.
