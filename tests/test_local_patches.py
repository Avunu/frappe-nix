# SPDX-License-Identifier: MIT
"""Tests for behaviour this package adds on top of upstream.

test_frappe_runtime.py is regenerated from the pinned Frappe commit by
scripts/sync-upstream.sh and must stay recognisably upstream's. Anything our own
patches introduce is covered here instead, where sync cannot overwrite it.
"""

import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

# Mirrors the stub in the generated suite: import the modules under test without
# requiring the real socketio, but defer to a real install when there is one.
if "socketio" not in sys.modules:
	import importlib.util

	if importlib.util.find_spec("socketio") is None:
		_sio_mod = types.ModuleType("socketio")
		_exc_mod = types.ModuleType("socketio.exceptions")

		class ConnectionRefusedError(Exception):
			pass

		_exc_mod.ConnectionRefusedError = ConnectionRefusedError
		_sio_mod.exceptions = _exc_mod
		sys.modules["socketio"] = _sio_mod
		sys.modules["socketio.exceptions"] = _exc_mod

import socketio

from frappe_runtime import bridge as bridge_mod
from frappe_runtime.config import RealtimeConfig

HAS_SOCKETIO = hasattr(socketio, "AsyncServer")


def make_config(**kwargs) -> RealtimeConfig:
	base = {"port": 9000, "redis_queue": "redis://127.0.0.1:11311"}
	base.update(kwargs)
	return RealtimeConfig(**base)


@unittest.skipUnless(HAS_SOCKETIO, "needs a real python-socketio")
class TestClientManagerSelection(unittest.TestCase):
	"""create_sio picks the manager the config asks for."""

	def test_defaults_to_in_process_rooms(self):
		from frappe_runtime.server import TolerantManager, create_sio

		sio = create_sio(make_config())
		self.assertIsInstance(sio.manager, TolerantManager)
		# Not a redis one: the single-process default must not open a connection.
		self.assertNotIsInstance(sio.manager, socketio.AsyncRedisManager)

	def test_redis_manager_when_asked(self):
		from frappe_runtime.server import TolerantRedisManager, create_sio

		sio = create_sio(make_config(redis_manager=True))
		self.assertIsInstance(sio.manager, TolerantRedisManager)
		self.assertIsInstance(sio.manager, socketio.AsyncRedisManager)

	def test_duplicate_connect_tolerance_survives_both(self):
		"""The CONNECT_ERROR fix is a mixin precisely so it is not lost with redis."""
		from frappe_runtime.server import TolerantManager, TolerantRedisManager

		for cls in (TolerantManager, TolerantRedisManager):
			with self.subTest(manager=cls.__name__):
				self.assertIs(
					cls.connect,
					TolerantManager.connect,
					"both managers must share the tolerant connect()",
				)


class TestBridgeStaysLocal(unittest.IsolatedAsyncioTestCase):
	"""Every process runs a bridge, so a bridge emit must not re-enter the queue.

	Without ignore_queue a redis-backed manager would re-publish each frappe event
	once per process, and every client would see it N times.
	"""

	def setUp(self):
		self.sio = MagicMock()
		self.sio.emit = AsyncMock()
		self.bridge = bridge_mod.RedisBridge(self.sio, "redis://x")

	async def test_room_emit_ignores_queue(self):
		await self.bridge._handle(
			'{"namespace": "s1", "room": "user:a", "event": "msg", "message": {"k": 1}}'
		)
		self.assertTrue(self.sio.emit.call_args.kwargs["ignore_queue"])

	async def test_broadcast_ignores_queue(self):
		self.sio.manager.rooms = {"/s1": {}, "/s2": {}}
		await self.bridge._handle('{"namespace": "s1", "event": "build", "message": {"k": 1}}')
		self.assertEqual(self.sio.emit.call_count, 2)
		for call in self.sio.emit.call_args_list:
			self.assertTrue(call.kwargs["ignore_queue"])


class TestConfigSwitch(unittest.TestCase):
	def test_redis_manager_defaults_off(self):
		self.assertFalse(make_config().redis_manager)


if __name__ == "__main__":
	unittest.main()
