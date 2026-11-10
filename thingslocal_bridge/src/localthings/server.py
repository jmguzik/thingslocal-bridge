"""
Copyright (C) 2024 LocalThings contributors

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import asyncio, ssl, struct, json, secrets, argparse, re, hashlib, os
from getpass import getpass
from enum import IntEnum, IntFlag, Enum
from functools import partial
from uuid import uuid4
from pathlib import Path
from base64 import urlsafe_b64encode, b64decode, b64encode
from hashlib import sha256
from typing import Optional

import structlog

class ControlPacketType(IntEnum):
	""" MQTT control packet types """
	CONNECT = 1
	CONNACK = 2
	PUBLISH = 3
	PUBACK = 4
	PUBREC = 5
	PUBREL = 6
	PUBCOMP = 7
	SUBSCRIBE = 8
	SUBACK = 9
	UNSUBSCRIBE = 10
	UNSUBACK = 11
	PINGREQ = 12
	PINGRESP = 13
	DISCONNECT = 14
	AUTH = 15

class ConnectReturnCode(IntEnum):
	""" MQTT CONNACK return codes """
	ACCEPTED = 0
	UNACCEPTABLE_PROTOCOL_VERSION = 1
	IDENTIFIER_REJECTED = 2
	SERVER_UNAVAILABLE = 3
	BAD_USER_PASSWORD = 4
	NOT_AUTHORIZED = 5

class FixedLengthStreamReader:
	def __init__ (self, reader, remaining=None):
		self._reader = reader
		self._remaining = remaining

	async def read(self, n=-1):
		if self._remaining == 0:
			return b''

		if self._remaining is None:
			# No limitation
			buf = await self._reader.read (n)
		else:
			if n == -1:
				buf = await self._reader.read (self._remaining)
			else:
				buf = await self._reader.read (min(self._remaining, n))
			self._remaining -= len(buf)
		return buf

	def limit (self, remaining):
		return FixedLengthStreamReader (self, remaining)

	async def readByte (self):
		buf = await self.read(1)
		if not buf:
			raise ValueError()
		return buf[0]

	async def readVariableByteInteger(self):
		multiplier = 1
		value = 0
		while True:
			encodedByte = await self.readByte()
			value += (encodedByte & 127) * multiplier

			if multiplier > 128*128*128:
				raise ValueError('Malformed Variable Byte Integer')
			multiplier *= 128
			if (encodedByte & 128) == 0:
				break
		return value

	async def readUtf8String(self):
		data = await self.readBinaryData()
		return data.decode ('utf-8')

	async def readBinaryData(self):
		length = await self.readUint16()
		data = await self.read (length)
		if len (data) != length:
			raise ValueError(f'Data too short, expected {length} got {len(data)}')
		return data

	async def readUint16 (self):
		buf = await self.read(2)
		if len (buf) < 2:
			raise ValueError(f'Binary data too short, got {len(buf)} bytes, expected 2')
		value, = struct.unpack('>H', buf)
		return value

def encodeVariableByteInteger(value):
	ret = []
	while True:
		encodedByte = value % 128
		value = value // 128
		# if there are more data to encode, set the top bit of this byte
		if value > 0:
			encodedByte = encodedByte | 128
		ret.append (encodedByte)
		if value <= 0:
			break
	return bytes(ret)

def encodeUtf8String (value) -> bytes:
	return struct.pack('>H', len(value)) + value.encode('utf-8')

# XXX: We should validate the topic.
class Topic:
	def __init__ (self, s: str):
		self.parts = s.split ('/')

	def __str__ (self):
		return '/'.join (self.parts)

	def __repr__ (self):
		return repr(str(self))

	def __eq__ (self, other):
		if isinstance (other, Topic):
			return self.parts == other.parts
		elif isinstance (other, str):
			return self == Topic(other)
		else:
			raise NotImplemented

	def __hash__ (self):
		return hash ('/'.join(self.parts))

class Subscription:
	def __init__ (self, s: str):
		split = s.split ('/')
		for part in split:
			if len(part) > 1 and (part.endswith ('#') or part.endswith('+')):
				raise ValueError(f'{part} ends with invalid character')
		self.parts = [self._parsePart(x) for x in split]
		multi = [x is self._matchMulti for x in self.parts]
		if sum(multi) > 1:
			raise ValueError('More than one multi-level wildcard')
		if sum(multi) == 1 and multi[-1] != True:
			raise ValueError('Multi-level wildcard is not the last path part')
		self._str = s

	def __str__ (self):
		return self._str

	def __repr__ (self):
		return repr(str(self))

	def _parsePart (self, s):
		lookup = {'+': self._matchSingle, '#': self._matchMulti}
		if s in lookup:
			return lookup[s]
		return self._matchExact(s)

	@staticmethod
	def _matchSingle(matched: list[str], unmatched: list[str]):
		""" Single level wildcard. """
		# Does not match previous topic level
		if len(unmatched) == 0:
			return False, 0
		# Does not match topics starting with $
		if len(matched) == 0 and unmatched[0].startswith('$'):
			return False, 0
		return True, 1

	@staticmethod
	def _matchMulti(matched: list[str], unmatched: list[str]):
		""" Multi-level wildcard. """
		# Matches previous topic level
		if len(unmatched) == 0:
			return True, 0
		if len(matched) == 0 and unmatched[0].startswith('$'):
			return False, 0
		return True, len(unmatched)

	@staticmethod
	def _matchExact(value: str):
		def f(matched: list[str], unmatched: list[str]):
			if len(unmatched) > 0 and unmatched[0] == value:
				return True, 1
			return False, 0
		return f

	def match (self, topic: Topic):
		matched = []
		unmatched = topic.parts
		for matcher in self.parts:
			isMatch, take = matcher(matched, unmatched)
			if not isMatch:
				return False
			matched.extend(unmatched[take:])
			unmatched = unmatched[take:]
		return len(unmatched) == 0

class Device:
	def __init__ (self, serialNumber, id, name, lookupId, mnId, vid, dip, events=None):
		"""
		The id (=deviceId) is assigned by us, the name is the label assigned by the user and lookupId is assigned by the device itself.
		"""
		self.serialNumber = serialNumber
		self.id = id
		self.name = name
		self.lookupId = lookupId
		self.mnId = mnId
		self.vid = vid
		self.dip = dip
		self.events = events or dict()

	def __repr__ (self):
		return f'<Device name={self.name} serialNumber={self.serialNumber}>'

	def addEvent (self, e):
		key = '/'.join([e['component'], e['capability'], e['attribute']])
		self.events[key] = e

	def toDict (self):
		# No need to persist events, since they will be sent again if the device re-connects
		return dict (serialNumber=self.serialNumber, id=self.id, name=self.name, lookupId=self.lookupId, mnId=self.mnId, vid=self.vid, dip=self.dip)

	@classmethod
	def fromDict (cls, d):
		return cls(serialNumber=d['serialNumber'], id=d['id'], name=d['name'], lookupId=d['lookupId'], mnId=d['mnId'], vid=d['vid'], dip=d['dip'])

class Client:
	def __init__ (self, connection):
		self._connection = connection
		self._logger = self._connection._logger

	async def onSubscribe(self, topics: list[tuple[Subscription, int]]):
		pass

	async def onPublish (self, topic, message):
		pass

class ClientDeviceNew(Client):
	def __init__ (self, connection, server, username):
		super().__init__ (connection)
		self._server = server
		self._username = username

	def __repr__ (self):
		return f'<ClientDeviceNew username={self._username}>'

	async def onSubscribe (self, topics):
		regNotifyTopic = Topic(f'/v1/registrations/notification/{self._username}')
		for subscription, _ in topics:
			if subscription.match(regNotifyTopic):
				event = {'event': 'connect.success'}
				await self._connection.publish(regNotifyTopic, event, qos=1)
				# Send at most once.
				break

	async def onPublish (self, topic, message):
		if topic == '/v1/registrations':
			# Message posted looks like this:
			# {'label': 'foobar', 'mnId': 'XXX', 'vid': 'XXX', 'deviceTypeId': 'Dishwasher', 'lookupId': '<uuid>', 'serialHash': '<base64 serialnumber>', 'provisioningTs': <unix timestamp>, 'firmwareVersion': '<firmware version>', 'osType': 'TizenRT', 'osVersion': '2.0', 'stdkVersion': '1.7.4', 'deviceIntegrationProfileKey': {'id': '<uuid>', 'majorVersion': 2, 'minorVersion': 0}}

			hashedSerial = urlsafe_b64encode(sha256(self._username.encode('ascii')).digest()).decode('ascii')
			canRegister = self._server.canRegister (self._username, message['label'])
			if canRegister and hashedSerial == message.get('serialHash'):
				deviceId = str(uuid4())
				event = {'deviceId': deviceId}
				device = Device(serialNumber=self._username, id=deviceId, name=message['label'], lookupId=message['lookupId'], mnId=message['mnId'], vid=message['vid'], dip=message['deviceIntegrationProfileKey'])
				self._server.registerDevice(device)
				self._logger.debug('localthings.register', request=message, deviceId=deviceId)

				deviceTopic = Topic(f'/localthings/{device.name}')
				tasks = []
				for c in self._server._connections:
					for subscription in c._subscriptions.keys():
						if subscription.match(deviceTopic):
							tasks.append (asyncio.create_task (c.publish (deviceTopic, device.toDict(), qos=1), name=f'device-{c}'))
							# Send this event only once.
							break
				if tasks:
					await asyncio.wait (tasks)
			else:
				event = {'event': 'error'}
				self._logger.error('localthings.register.unauthorized', request=message, canRegister=canRegister, hashedSerial=hashedSerial)
			await self._connection.publish(f'/v1/registrations/notification/{self._username}', event, qos=1)
		else:
			self._logger.warn('localthings.unknowndevicetopic', topic=topic, message=message)

class ClientDeviceRegistered(Client):
	def __init__ (self, connection, server, device):
		super().__init__ (connection)
		self._server = server
		self._device = device

	def __repr__ (self):
		return f'<ClientDeviceRegistered device={self._device}>'

	async def onSubscribe (self, topics):
		notifyTopic = Topic(f'/v1/notifications/{self._device.id}')
		for subscription, _ in topics:
			if subscription.match(notifyTopic):
				# Technically this is only required during the provisioning process,
				# but we just send it every time, which does not hurt.
				event = {'event': 'connect.success'}
				await self._connection.publish(notifyTopic, event, qos=1)

	async def onPublish (self, topic, message):
		if topic == f'/v1/deviceEvents/{self._device.id}':
			self._logger.debug('localthings.deviceevents', request=message)
			for e in message['deviceEvents']:
				device = self._device
				device.addEvent (e)
				# See if anyone subscribed to that event.
				eventTopic = Topic(f'/localthings/{device.name}/events/{e["component"]}/{e["capability"]}/{e["attribute"]}')
				# Enqueue tasks, so we don’t need a mutex on _connections and _subscriptions.
				tasks = []
				for c in self._server._connections:
					for subscription in c._subscriptions.keys():
						if subscription.match(eventTopic):
							tasks.append (asyncio.create_task (c.publish (eventTopic, e, qos=1), name=f'forward-{c}'))
							# Send this event only once.
							break
				if tasks:
					self._logger.debug('localthings.forwardevent', topic=eventTopic)
					await asyncio.wait (tasks)
		else:
			self._logger.warn('localthings.unknowndevicetopic', topic=topic, message=message)

class ClientConsumer(Client):
	def __init__ (self, connection, server):
		super().__init__ (connection)
		self._server = server

	async def onSubscribe (self, topics):
		# Send all matching stored events.
		sent = set()
		tasks = []
		for subscription, _ in topics:
			for d in self._server._registeredDevices.values():
				deviceTopic = Topic(f'/localthings/{d.name}')
				if not deviceTopic in sent and subscription.match(deviceTopic):
					tasks.append (asyncio.create_task (self._connection.publish (deviceTopic, d.toDict(), qos=1), name=f'initialDevice-{self}'))
					sent.add (deviceTopic)
				for e in d.events.values():
					eventTopic = Topic(f'/localthings/{d.name}/events/{e["component"]}/{e["capability"]}/{e["attribute"]}')
					if not eventTopic in sent and subscription.match(eventTopic):
						tasks.append (asyncio.create_task (self._connection.publish (eventTopic, e, qos=1), name=f'initialForward-{self}'))
						sent.add(eventTopic)
		if tasks:
			await asyncio.wait (tasks)

	async def onPublish (self, topic, message):
		found = False
		for c in self._server._connections:
			if not isinstance(c._client, ClientDeviceRegistered):
				continue
			d = c._client._device
			if Subscription(f'/localthings/{d.name}/command/#').match(topic):
				found = True

				# We found the correct device
				try:
					component, capability, command = topic.parts[4:]
					if not component or not capability or not command:
						raise ValueError()
				except ValueError:
					self._logger.info('localthings.command.invalid', connection=c, topic=topic, message=message)
					break
				deviceEvent = {"commands": [{
						"component": component,
						"capability": capability,
						"command": command,
						"arguments": message,
						"id": str(uuid4()),
						}]}
				self._logger.info('localthings.command', deviceName=d.name, deviceEvent=deviceEvent)
				await c.publish (Topic(f'/v1/commands/{d.id}'), deviceEvent, qos=1)

				# Publish at most once, even if the device is connected multiple times
				break
		if not found:
			self._logger.warn('localthings.command.devicenotconnected', topic=topic, message=message)

class Connection:
	DEFAULT_KEEPALIVE = 5*60
	CONNECT_KEEPALIVE = 60

	def __init__ (self, logger, clientFactory, reader, writer, extraKeepalive):
		self._logger = logger
		self._reader = reader
		self._writer = writer
		self._clientFactory = clientFactory
		self._extraKeepalive = extraKeepalive

		self._client = None

		self._version = None
		self._queuedPackets = asyncio.Queue()
		self._subscriptions = dict()
		self._keepAlive = self.CONNECT_KEEPALIVE
		self._closeConnection = False

	def __repr__ (self):
		return f'<Connection client={self._client}>'

	async def handleConnect (self, reader, writer, flags):
		name = await reader.read (6)
		if name != b'\x00\x04MQTT':
			self._logger.info ('mqtt.CONNECT.notmqtt')
			writer.close ()
			return

		self._version = await reader.readByte()
		if self._version != 4:
			self._logger.info ('mqtt.CONNECT.versionmismatch', offer=self._version)
			await self.queuePacket(ControlPacketType.CONNACK, bytes([0, ConnectReturnCode.UNACCEPTABLE_PROTOCOL_VERSION]))
			return

		flags = await reader.readByte()
		cleanStart = (flags&(1<<1)) != 0
		hasWill = (flags&(1<<2)) != 0
		hasPassword = (flags&(1<<6)) != 0
		hasUsername = (flags&(1<<7)) != 0

		self._keepAlive = await reader.readUint16()
		if self._keepAlive <= 0:
			# Only allow sensible keep alive values.
			self._keepAlive = self.DEFAULT_KEEPALIVE
		else:
			# The client might not be entirely punctual, so give it some extra time.
			self._keepAlive += self._extraKeepalive

		clientId = await reader.readUtf8String()
		if not clientId:
			clientId = str(uuid4())
		self._logger = self._logger.bind(clientId=clientId)

		if hasWill:
			willTopic = await reader.readUtf8String ()
			willData = await reader.readBinaryData()
			# We do not support a will and there is no status code for that, so the
			# specification says we should just close the connection.
			self.close ()
			return

		username = None
		if hasUsername:
			username = await reader.readUtf8String()

		password = None
		if hasPassword:
			password = await reader.readUtf8String()
			# The password for devices should be a JWT of the following format:
			# <base64 header>.<base64 data>.<ed25519 signature>
			# {"alg":"EdDSA","kty":"OKP","crv":"Ed25519","typ":"JWT","ver":"0.0.1","kid":"<serial number>"}
			# {"iat":"<timestamp>","jti":"<random uuid>","mnId":"<mnId>"}
			# Unfortunately SmartThings does not expose the device’s public key
			# anywhere, so we cannot verify the signature.

		# Consume everything else we missed (there should be nothing)
		buf = await reader.read()
		assert len(buf) == 0

		# A device either connects with its serial number as username or with the deviceId we gave it.
		self._client = self._clientFactory (self, username, password)
		if self._client is None:
			self._logger.info('mqtt.CONNECT.unauthorized', username=username, password=password)
			await self.queuePacket(ControlPacketType.CONNACK, bytes([0, ConnectReturnCode.BAD_USER_PASSWORD]))
			self.close()
			return

		self._logger.info('mqtt.CONNECT', keepAlive=self._keepAlive, username=username)

		await self.queuePacket(ControlPacketType.CONNACK, bytes([0, ConnectReturnCode.ACCEPTED]))

	async def handleSubscribe (self, reader, writer, flags):
		topics = []
		packetIdentifier = await reader.readUint16()
		while True:
			try:
				topic = await reader.readUtf8String()
			except ValueError:
				break
			qos = (await reader.readByte())&0x3
			topics.append ((topic, qos))

		acceptedTopics = []
		resp = []
		for t, q in topics:
			try:
				s = Subscription(t)
				self._subscriptions[s] = q
				acceptedTopics.append ((s, q))
				resp.append (q)
			except ValueError:
				resp.append(0x80)

		self._logger.info('mqtt.SUBSCRIBE', topics=topics, acceptedTopics=acceptedTopics, packetIdentifier=packetIdentifier, subscriptions=self._subscriptions)

		await self.queuePacket(ControlPacketType.SUBACK,
				bytes([(packetIdentifier>>8)&0xff, packetIdentifier&0xff] + resp))

		await self._client.onSubscribe(acceptedTopics)

	async def handlePingreq (self, reader, writer, flags):
		await self.queuePacket(ControlPacketType.PINGRESP)

	async def handleDisconnect (self, reader, writer, flags):
		self.close()

	async def handlePublish (self, reader, writer, flags):
		qos = (flags>>1)&0x3
		retain = (flags&0x1) != 0
		dup = ((flags>>3)&0x1) != 0
		topic = Topic(await reader.readUtf8String ())
		packetIdentifier = await reader.readUint16()
		message = await reader.read()

		self._logger.debug('mqtt.PUBLISH', topic=topic, retain=retain, dup=dup, qos=qos, message=message, packetIdentifier=packetIdentifier)

		if qos == 1:
			await self.queuePacket(ControlPacketType.PUBACK, bytes([(packetIdentifier>>8)&0xff, packetIdentifier&0xff]))
		elif qos == 2:
			assert False

		try:
			decodedMessage = json.loads (message)
		except Exception:
			self._logger.error('localthings.invalidjson', message=message, exc_info=True)
			return

		await self._client.onPublish (topic, decodedMessage)

	async def handlePuback (self, reader, writer, flags):
		packetIdentifier = await reader.readUint16 ()
		# Not implemented
		self._logger.debug('mqtt.PUBACK', packetIdentifier=packetIdentifier)

	async def publish (self, topic: Topic, message, qos=0, retain=False):
		# XXX: something is wrong with qos==0
		assert qos == 1
		if isinstance(message, dict):
			message = json.dumps(message).encode('utf-8')
		buf = encodeUtf8String(str(topic)) + secrets.token_bytes(2) + message
		flags = qos<<1 | int(retain)<<0
		await self.queuePacket(ControlPacketType.PUBLISH, buf, flags=flags)

	async def queuePacket (self, kind: ControlPacketType, data: bytes=None, flags=0):
		await self._queuedPackets.put ((kind, data, flags))

	async def run(self):
		reader = FixedLengthStreamReader (self._reader)

		getQueued = asyncio.create_task(self._queuedPackets.get(), name='initialGetQueued')
		readData = asyncio.create_task(reader.readByte(), name='initialReadData')
		pending = [getQueued, readData]
		while True:
			pending = [getQueued, readData]
			try:
				# Since close() does not fire an event, we might linger around until
				# keepAlive expires. That’s probably fine.
				done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED,
						timeout=self._keepAlive if not self._closeConnection else 0)
			except asyncio.CancelledError:
				break

			if not done:
				if self._closeConnection:
					# Done processing.
					pass
				else:
					# Timeout.
					self._logger.error('net.keepalivetimeout')
				break

			if getQueued in done:
				kind, data, flags = await getQueued
				resp = bytes([kind<<4|flags])
				if data is None:
					resp += b'\x00'
				else:
					resp += encodeVariableByteInteger(len(data)) + data
				self._logger.info('packet.send', type=kind, flags=flags, data=data)
				self._writer.write (resp)
				getQueued = asyncio.create_task(self._queuedPackets.get(), name='getQueued')
				# Sending packets has priority over receiving.
				continue

			if readData in done:
				try:
					buf = await readData
				except ValueError:
					# Client closed the connection
					self._logger.info('net.closed')
					break
				try:
					type = ControlPacketType(buf>>4)
				except ValueError:
					break
				flags = buf&0xf

				# Ensure the client sent a CONNECT before using other commands.
				if self._client is None and type != ControlPacketType.CONNECT:
					self._logger.error ('mqtt.noconnect')
					# Closes the connection.
					break

				length = await reader.readVariableByteInteger()
				packetReader = reader.limit (length)

				self._logger.debug ('packet.receive', type=type, flags=flags, length=length)

				dispatch = {
						ControlPacketType.CONNECT: self.handleConnect,
						ControlPacketType.SUBSCRIBE: self.handleSubscribe,
						ControlPacketType.PINGREQ: self.handlePingreq,
						ControlPacketType.DISCONNECT: self.handleDisconnect,
						ControlPacketType.PUBLISH: self.handlePublish,
						ControlPacketType.PUBACK: self.handlePuback,
					}

				await dispatch[type](packetReader, self._writer, flags)

				readData = asyncio.create_task(reader.readByte(), name='readData')
		try:
			getQueued.cancel()
			await getQueued
		except asyncio.CancelledError:
			pass
		try:
			readData.cancel()
			await readData
		except (ValueError, asyncio.CancelledError):
			pass

		try:
			self._writer.close ()
			await self._writer.wait_closed()
		except Exception:
			self._logger.warn('net.close.error', exc_info=True)

	def close (self):
		self._closeConnection = True

class MQTTServer:
	def __init__ (self, host, port, logger, sslContext=None, allowRegister=None, extraKeepalive=60, consumerUsers=None, maxConnections=10):
		self._logger = logger
		self._sslContext = sslContext
		self._host = host
		self._port = port
		self._extraKeepalive = extraKeepalive
		self._maxConnections = maxConnections

		self._connid = 0
		self._connections = set()
		self._registeredDevices = dict()
		self._allowRegister = allowRegister or dict()
		self._server = None
		self._consumerUsers = consumerUsers or {}

	async def onConnect (self, reader, writer):
		self._connid += 1
		logger = self._logger.bind (connid=self._connid)
		logger.info('net.connect', remote=writer.get_extra_info('peername'),
				cipher=writer.get_extra_info('cipher'),
				compression=writer.get_extra_info('compression'),
				connections=len(self._connections))

		if len (self._connections) >= self._maxConnections:
			logger.error('net.connect.limit', maxConnections=self._maxConnections)
			try:
				writer.close ()
				await writer.wait_closed()
			except Exception:
				logger.warn('net.close.error', exc_info=True)
			return

		conn = Connection (logger, self._clientFactory, reader, writer, self._extraKeepalive)
		self._connections.add (conn)
		try:
			await conn.run ()
		except Exception as e:
			logger.error('net.error', exc_info=True)
		finally:
			self._connections.remove (conn)
			logger.info('net.disconnect')

	async def __aenter__ (self):
		try:
			self._server = await asyncio.start_server(self.onConnect, self._host, self._port, ssl=self._sslContext)
		except PermissionError:
			self._logger.error('net.listen.permissiondenied', port=self._.port, host=self._port)
			raise
		except OSError:
			self._logger.error('net.listen.error', port=self._port, host=self._port, exc_info=True)
			raise

		await self._server.start_serving()
		self._logger.info('net.listen', port=self._port, host=self._host)

		return self

	async def __aexit__(self, exc_type, exc, tb):
		assert self._server is not None
		self._server.close()
		for c in self._connections:
			c.close()
		await self._server.wait_closed()
		self._server = None

	def canRegister (self, serialNumber, deviceName):
		return self._allowRegister is None or self._allowRegister.get(serialNumber) == deviceName

	def registerDevice (self, device):
		self._registeredDevices[device.id] = device
		del self._allowRegister[device.serialNumber]

	@staticmethod
	def decodeJWT(s: str):
		header, payload, signature = [b64decode(x) for x in s.split('.', 2)]
		header = json.loads(header)
		payload = json.loads(payload)
		return header, payload, signature

	def _clientFactory (self, connection, username, password: Optional[str]) -> Client:
		user = self._consumerUsers.get(username, None)
		if user is not None and password is not None and user['hash'] == hashlib.scrypt(password.encode('utf8'), salt=user['salt'], n=user['n'], r=user['r'], p=user['p']):
			return ClientConsumer(connection, self)
		elif username in self._registeredDevices and password is not None:
			try:
				header, payload, _ = self.decodeJWT(password)
			except Exception:
				self._logger.error('localthings.login.jwtinvalid', password=password, exc_info=True)
				return None
			device = self._registeredDevices[username]
			if header.get('kid') == device.serialNumber and payload.get('mnId') == device.mnId:
				return ClientDeviceRegistered(connection, self, device)
		elif username in self._allowRegister and password is not None:
			try:
				header, payload, _ = self.decodeJWT(password)
			except Exception as e:
				self._logger.error('localthings.login.jwtinvalid', password=password, exc_info=True)
				return None
			if header.get('kid') == username:
				return ClientDeviceNew(connection, self, username)

		return None

	def load (self, fd):
		for l in fd:
			# We cannot use registerDevice(), because it deletes allowRegister
			device = Device.fromDict(json.loads (l))
			self._registeredDevices[device.id] = device

	def save (self, fd):
		for v in self._registeredDevices.values():
			json.dump (v.toDict(), fd)
			fd.write ('\n')

async def run (logger, args):
	context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
	try:
		context.load_cert_chain(args.sslCert, args.sslKey)
	except FileNotFoundError:
		logger.error('certificatemissing', cert=args.sslCert, key=args.sslKey)
		return 1

	consumerUsers = {}
	consumerUsersFile = args.state / 'users.json'
	try:
		with consumerUsersFile.open() as fd:
			for l in fd:
				entry = json.loads (l)
				consumerUsers[entry['user']] = {'hash': b64decode(entry['hash']), 'salt': b64decode(entry['salt']), 'n': entry['n'], 'r': entry['r'], 'p': entry['p']}
	except FileNotFoundError:
		logger.info('noconsumers')

	server = MQTTServer(
			host=args.host,
			port=args.port,
			logger=logger,
			sslContext=context,
			allowRegister=dict(args.allowRegister),
			consumerUsers=consumerUsers,
			maxConnections=args.connections,
			)

	serverStateFile = args.state / 'devices.json'
	try:
		with serverStateFile.open() as fd:
			server.load (fd)
	except FileNotFoundError:
		logger.info('nostate')

	try:
		async with server:
			while True:
				await asyncio.sleep (60)
	finally:
		with open (serverStateFile.with_suffix('.json.tmp'), 'w') as fd:
			server.save (fd)
		serverStateFile.with_suffix('.json.tmp').rename(serverStateFile)

def createConsumerUser (username, password):
	salt = os.urandom(32)
	n = 16384
	r = 8
	p = 1
	return {
			'hash': hashlib.scrypt(password.encode('utf-8'), salt=salt, n=n, r=r, p=p),
			'salt': salt,
			'n': n,
			'r': r,
			'p': p,
			'user': username,
			}

async def createUser (logger, args):
	password = getpass()
	args.state.mkdir(exist_ok=True)
	with (args.state / 'users.json').open('a') as fd:
		data = createConsumerUser(args.username, password)
		data['hash'] = b64encode(data['hash']).decode('ascii')
		data['salt'] = b64encode(data['salt']).decode('ascii')
		json.dump (data, fd)
		fd.write('\n')

async def asyncmain ():
	logger = structlog.get_logger()

	parser = argparse.ArgumentParser(description='SmartThings-compatible MQTT server implementation.')
	parser.add_argument('--state', metavar='DIRECTORY', help='Save server state to this directory.', type=Path, required=True)

	subparsers = parser.add_subparsers(required=True)

	parserRun = subparsers.add_parser('run', help='Run the server.')
	parserRun.add_argument('--host', default='localhost', help='Listen address')
	parserRun.add_argument('--port', default=8883, type=int, help='Listen port')
	parserRun.add_argument('--ssl-key', default='server.key', help='Private SSL key in PEM format', dest='sslKey')
	parserRun.add_argument('--ssl-cert', default='server.crt', help='Public certificate chain in PEM format', dest='sslCert')
	parserRun.add_argument('--allow-register', action='append', metavar='SERIALNUMBER=DEVICENAME', help='Allow the device with this serial number and device name to register.', type=lambda x: x.split('=', 1), default=[], dest='allowRegister')
	parserRun.add_argument('--connections', default=10, type=int, help='Limit number of concurrent connections.')
	parserRun.set_defaults(func=run)

	parserCreateUser = subparsers.add_parser('create-user', help='Create a new consumer user.')
	parserCreateUser.add_argument('username', help='New user’s name')
	parserCreateUser.set_defaults(func=createUser)

	args = parser.parse_args()

	return await args.func (logger, args)

def main ():
	return asyncio.run (asyncmain())

