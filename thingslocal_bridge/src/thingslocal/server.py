"""Persistent STDK telemetry with explicitly enabled dishwasher program selection."""
import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import ssl
from uuid import uuid4

import structlog
from localthings.server import ClientDeviceRegistered, Device, MQTTServer, Topic
from . import __version__
from .bridge import MQTTBridge
from .security import verify_certificate
from .settings import atomic_json, device_identifier, load_registry, validate, validate_registry
from .telemetry import DeviceTelemetry
from .controls import ControlRejected, DishwasherCourseControl, course_enabled
from .diagnostics import DishwasherDiagnostics


def redact(logger, method, event):
    if event.get('event') == 'mqtt.SUBSCRIBE':
        event['subscriptions'] = {str(topic): qos for topic, qos in event['subscriptions'].items()}
    for key in ('password', 'request', 'message', 'data', 'username', 'clientId'):
        event.pop(key, None)
    return event


class ApplianceServer(MQTTServer):
    def __init__(self, config, directory, context, logger, status, changed):
        self.config, self.directory, self.context = config, Path(directory), context
        self.selected = {device['identity']['serial']: device for device in config['devices']}
        self.by_ip = {device['ip']: device for device in config['devices']}
        self.status, self.changed = status, changed
        super().__init__(config['broker'].get('listen_host', '0.0.0.0'),
                         config['broker'].get('port', 8883), logger, sslContext=None,
                         allowRegister={serial: device['label'] for serial, device in self.selected.items()},
                         maxConnections=max(3, len(self.selected) * 3))
        for record in load_registry(self.directory / 'registrations.json', config):
            device = Device.fromDict(record); self._registeredDevices[device.id] = device

    def _clientFactory(self, connection, username, password):
        peer = connection._writer.get_extra_info('peername')
        expected = self.by_ip.get(peer[0]) if peer else None
        registered = self._registeredDevices.get(username)
        serial = registered.serialNumber if registered else username
        if expected is None or serial != expected['identity']['serial']: return None
        client = super()._clientFactory(connection, username, password)
        if client is not None:
            connection.thingslocal_session = structlog.get_context(connection._logger).get('connid')
        return client

    def command_connection(self, device_id, session):
        selected = next((device for device in self.config['devices'] if device['id'] == device_id), None)
        if not selected or not course_enabled(selected) or session is None:
            raise ControlRejected('control_disabled')
        matches = []
        for connection in self._connections:
            client = connection._client
            if (not isinstance(client, ClientDeviceRegistered)
                    or client._device.serialNumber != selected['identity']['serial']
                    or getattr(connection, 'thingslocal_session', None) != session): continue
            peer = connection._writer.get_extra_info('peername')
            if (not peer or peer[0] != selected['ip'] or connection._writer.is_closing()
                    or connection._closeConnection): continue
            topic = Topic('/v1/commands/' + client._device.id)
            if any(subscription.match(topic) for subscription in connection._subscriptions):
                matches.append((connection, topic))
        if len(matches) != 1: raise ControlRejected('session_unavailable')
        return matches[0]

    def command_ready(self, device_id, session):
        try: self.command_connection(device_id, session); return True
        except ControlRejected: return False

    async def select_course(self, device_id, session, course):
        connection, topic = self.command_connection(device_id, session)
        await connection.publish(topic, {'commands': [{
            'component': 'main', 'capability': 'samsungce.dishwasherWashingCourse',
            'command': 'setWashingCourse', 'arguments': [course], 'id': str(uuid4()),
        }]}, qos=1)

    def registerDevice(self, device):
        selected = self.selected.get(device.serialNumber)
        if (selected is None or device.mnId != selected['identity']['manufacturer']
                or device.name != selected['label']):
            raise ValueError('Registration does not match the selected appliance')
        records = [item.toDict() for item in self._registeredDevices.values()
                   if item.serialNumber != device.serialNumber] + [device.toDict()]
        validate_registry(records, self.config)
        # Durable before the caller can send the new deviceId to the appliance.
        atomic_json(self.directory / 'registrations.json', records)
        for identifier, old in list(self._registeredDevices.items()):
            if old.serialNumber == device.serialNumber: del self._registeredDevices[identifier]
        super().registerDevice(device)
        self.status['devices'][selected['id']]['registered'] = True
        self.changed()

    async def onConnect(self, reader, writer):
        peer = writer.get_extra_info('peername')
        if peer is None or peer[0] not in self.by_ip:
            writer.close(); return
        device_status = self.status['devices'][self.by_ip[peer[0]]['id']]
        device_status['tcp_connections'] += 1; self.changed()
        try: await writer.start_tls(self.context, ssl_handshake_timeout=5)
        except (ssl.SSLError, ConnectionError, TimeoutError) as error:
            device_status['tls_errors'].append(getattr(error, 'reason', None) or type(error).__name__)
            device_status['tls_errors'] = device_status['tls_errors'][-10:]
            self.changed(); writer.close(); return
        device_status['tls_connections'] += 1; self.changed()
        await super().onConnect(reader, writer)


async def serve(config_path, directory):
    os.umask(0o077)
    config = validate(json.loads(Path(config_path).read_text()))
    if not config['devices']: raise ValueError('Configure at least one appliance')
    directory = Path(directory); directory.mkdir(mode=0o700, parents=True, exist_ok=True); directory.chmod(0o700)
    context, expiry = verify_certificate(config)
    states = {device['id']: DeviceTelemetry(device) for device in config['devices']}
    bridges = {device['id']: MQTTBridge(config, device, expiry) for device in config['devices']}
    diagnostics = {device['id']: DishwasherDiagnostics(device_identifier(config, device), directory)
                   for device in config['devices'] if device['profile'] == 'dishwasher'}
    status = {'version': __version__, 'installation_id': config['installation_id'],
              'listening': False, 'read_only': not any(course_enabled(d) for d in config['devices']),
              'certificate_expiry': expiry,
              'devices': {device['id']: {'registered': False, 'online': False,
                                       'tcp_connections': 0, 'tls_connections': 0,
                                       'tls_errors': [], 'event_batches': 0}
                          for device in config['devices']}}
    sessions = {}; server = None
    controls = {}

    def save(): atomic_json(directory / 'status.json', status)

    def update(device_id):
        state = states[device_id]
        values, entities = dict(state.values), dict(state.entities)
        if device_id in diagnostics:
            diagnostic = diagnostics[device_id]
            values.update(diagnostic.values); entities.update(diagnostic.entities)
            bridges[device_id].update_notification(diagnostic.notification)
        bridges[device_id].update(values, state.online, entities)
        status['devices'][device_id]['online'] = state.online
        save()

    def update_control(device_id):
        bridges[device_id].update_control(controls[device_id].snapshot())

    for device in config['devices']:
        device_id = device['id']
        if course_enabled(device):
            controls[device_id] = DishwasherCourseControl(device,
                lambda identifier=device_id: update_control(identifier),
                lambda session, identifier=device_id: server is not None and server.command_ready(identifier, session))

    def observe(logger, method, event):
        name, session = event.get('event'), event.get('connid')
        if name == 'mqtt.CONNECT':
            device = server._registeredDevices.get(event.get('username'))
            if device is not None:
                selected = server.selected[device.serialNumber]; device_id = selected['id']
                sessions[session] = device_id; states[device_id].begin(session)
                if device_id in diagnostics: diagnostics[device_id].begin(session)
                update(device_id)
                if device_id in controls:
                    controls[device_id].begin(session); update_control(device_id)
        elif name == 'localthings.deviceevents' and session in sessions:
            device_id = sessions[session]; state = states[device_id]
            events = event.get('request', {}).get('deviceEvents', [])
            changed = state.accept(session, events)
            if device_id in diagnostics: diagnostics[device_id].accept(session, events)
            if changed or device_id in diagnostics: update(device_id)
            if device_id in controls:
                controls[device_id].accept(session, event.get('request', {}).get('deviceEvents', []))
                update_control(device_id)
            status['devices'][device_id]['event_batches'] += 1
            atomic_json(directory / ('catalog-' + device_id + '.json'), list(state.catalog.values()))
            save()
        elif name == 'net.disconnect':
            device_id = sessions.pop(session, None)
            if device_id is not None and states[device_id].disconnect(session):
                if device_id in diagnostics: diagnostics[device_id].disconnect(session)
                update(device_id)
            if device_id in controls:
                controls[device_id].disconnect(session); update_control(device_id)
        elif name == 'mqtt.SUBSCRIBE' and session in sessions:
            device_id = sessions[session]
            if device_id in controls: update_control(device_id)
        return event

    handler = RotatingFileHandler(directory / 'bridge.log', maxBytes=1048576, backupCount=3)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logging.getLogger('thingslocal').handlers[:] = [handler]
    logging.getLogger('thingslocal').setLevel(logging.INFO)
    logging.getLogger('thingslocal').propagate = False
    structlog.configure(processors=[observe, redact, structlog.processors.TimeStamper(fmt='iso'),
                                   structlog.processors.format_exc_info, structlog.processors.JSONRenderer()],
                        logger_factory=structlog.stdlib.LoggerFactory(),
                        wrapper_class=structlog.stdlib.BoundLogger, cache_logger_on_first_use=False)
    server = ApplianceServer(config, directory, context, structlog.get_logger('thingslocal'), status, save)
    for device in server._registeredDevices.values(): status['devices'][server.selected[device.serialNumber]['id']]['registered'] = True
    stopped = asyncio.Event(); loop = asyncio.get_running_loop()
    command_tasks = set()

    def receive_course(device_id, course, epoch, session):
        bridge, control = bridges[device_id], controls[device_id]
        if stopped.is_set() or not bridge.command_current(epoch) or control.session != session:
            bridge.reject_command('session_changed'); bridge.command_done(); return

        async def send(current_session, selected_course):
            if (stopped.is_set() or not bridge.command_current(epoch)
                    or current_session != session or control.session != session):
                raise ControlRejected('session_changed')
            await server.select_course(device_id, session, selected_course)

        async def execute():
            try: await control.select(course, send)
            except Exception:
                logging.getLogger('thingslocal').exception('Program selection handler failed')
                bridge.reject_command('internal_error')
            finally: bridge.command_done()

        task = asyncio.create_task(execute()); command_tasks.add(task)
        task.add_done_callback(command_tasks.discard)

    def receive_acknowledgement(device_id, identifier):
        try:
            diagnostics[device_id].acknowledge(identifier)
            bridges[device_id].update_notification(diagnostics[device_id].notification)
        finally: bridges[device_id].acknowledgement_done()

    for device_id in diagnostics:
        bridges[device_id].set_acknowledgement_handler(
            lambda identifier, selected=device_id:
                loop.call_soon_threadsafe(receive_acknowledgement, selected, identifier))

    for device_id in controls:
        bridges[device_id].set_command_handler(
            lambda course, epoch, session, identifier=device_id:
                loop.call_soon_threadsafe(receive_course, identifier, course, epoch, session))
    for sig in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(sig, stopped.set)
    for device_id, bridge in bridges.items():
        if device_id in diagnostics: bridge.update_notification(diagnostics[device_id].notification)
        bridge.start()
    try:
        async with server:
            status['listening'] = True; save()
            print(json.dumps({'thingslocal_ready': True, 'devices': len(config['devices']),
                              'read_only': status['read_only']}), flush=True)
            await stopped.wait()
    finally:
        for task in command_tasks: task.cancel()
        await asyncio.gather(*command_tasks, return_exceptions=True)
        for device_id, control in controls.items():
            control.disconnect(control.session); update_control(device_id)
        for device_id, state in states.items(): bridges[device_id].update({}, False, state.entities)
        await asyncio.gather(*(asyncio.to_thread(bridge.stop) for bridge in bridges.values()))
        status['listening'] = False
        for value in status['devices'].values(): value['online'] = False
        save()
