"""Opt-in dishwasher program selection, confirmed by fresh appliance reports."""
import asyncio
import re

COURSE = ('samsungce.dishwasherWashingCourse', 'washingCourse')
OPTIONS = ('samsungce.dishwasherWashingCourse', 'supportedCourses')
POWER = ('switch', 'switch')
OPERATION = ('samsungce.dishwasherOperation', 'operatingState')
JOB = ('samsungce.dishwasherJobState', 'dishwasherJobState')
TOKEN = re.compile(r'[A-Za-z0-9_.-]{1,64}\Z')


def course_enabled(device):
    return device['profile'] == 'dishwasher' and device.get('controls', {}).get('dishwasher_course') is True


class ControlRejected(Exception):
    pass


class DishwasherCourseControl:
    def __init__(self, device, changed, connection_ready, timeout=10):
        self.enabled = course_enabled(device)
        self.changed, self.connection_ready, self.timeout = changed, connection_ready, timeout
        self.session = None
        self.values = {}
        self.pending = None
        self.uncertain = False
        self.result = 'waiting_for_state'

    def begin(self, session):
        self._interrupt()
        self.session = session; self.values = {}; self.result = 'waiting_for_state'; self.uncertain = False

    def disconnect(self, session):
        if session != self.session: return
        self._interrupt(); self.session = None; self.values = {}; self.result = 'disconnected'

    def _interrupt(self):
        if self.pending and not self.pending[2].done():
            self.pending[2].set_exception(ControlRejected('session_changed'))

    def accept(self, session, events):
        if session != self.session or session is None or not isinstance(events, list): return
        for event in events:
            if not isinstance(event, dict) or event.get('component') != 'main': continue
            key = event.get('capability'), event.get('attribute')
            value = event.get('value')
            if key == OPTIONS:
                if (isinstance(value, list) and 0 < len(value) <= 64
                        and all(isinstance(item, str) and TOKEN.fullmatch(item) for item in value)):
                    self.values[key] = list(dict.fromkeys(value))
                else: self.values.pop(key, None)
            elif key in (COURSE, POWER, OPERATION, JOB):
                if isinstance(value, str) and TOKEN.fullmatch(value): self.values[key] = value
                else: self.values.pop(key, None)
                if key == COURSE and key in self.values: self.uncertain = False
                if (key == COURSE and self.pending and self.pending[:2] == (session, value)
                        and not self.pending[2].done()):
                    self.pending[2].set_result(True)

    def snapshot(self):
        options = self.values.get(OPTIONS, [])
        value = self.values.get(COURSE)
        ready = (self.enabled and self.session is not None and self.pending is None and not self.uncertain
                 and self.values.get(POWER) == 'on' and self.values.get(OPERATION) == 'ready'
                 and self.values.get(JOB) == 'none' and value in options
                 and self.connection_ready(self.session))
        return {'enabled': self.enabled, 'options': list(options), 'value': value,
                'available': bool(ready),
                'result': 'ready' if ready and self.result == 'waiting_for_state' else self.result,
                'session': self.session}

    async def select(self, course, send):
        if self.pending is not None: return
        snapshot = self.snapshot()
        if not snapshot['available']:
            self.result = 'not_ready'; self.changed(); return
        if not isinstance(course, str) or course not in snapshot['options']:
            self.result = 'unsupported_program'; self.changed(); return
        if course == snapshot['value']:
            self.result = 'unchanged'; self.changed(); return
        session = self.session
        future = asyncio.get_running_loop().create_future()
        pending = (session, course, future)
        self.pending = pending; self.result = 'pending'; self.changed()
        try:
            await send(session, course)
            await asyncio.wait_for(future, timeout=self.timeout)
            self.result = 'confirmed'
        except TimeoutError:
            self.result = 'timeout_unknown'; self.uncertain = True
        except ControlRejected as error:
            self.result = str(error)
        except asyncio.CancelledError:
            self.result = 'interrupted'; raise
        except (OSError, ValueError):
            self.result = 'send_failed_unknown'; self.uncertain = True
        finally:
            if not future.done(): future.cancel()
            elif not future.cancelled(): future.exception()
            if self.pending is pending: self.pending = None
            self.changed()
