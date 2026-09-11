# This file is part of Meshtastic mesh observer.
#
# Copyright (c) 2025 Michael Wolf <michael@mictronics.de>
#
# Mesh observer is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# Mesh observer is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with mesh observer. If not, see http://www.gnu.org/licenses/.
#
import threading
import time
from typing import Final

from meshtastic.tcp_interface import TCPInterface
from pubsub import pub

# How long to wait for a fresh "connection.established" after "connection.lost"
# before assuming the link is really dead (a device reboot also fires "lost"
# and then resyncs in place over the same socket, so a short grace period
# avoids tearing down a connection that's about to heal itself).
RECONNECT_GRACE_SEC = 10
BACKOFF_START_SEC = 2
BACKOFF_MAX_SEC = 60


class TcpReader:
    """Owns the single upstream TCPInterface connection to the Meshtastic node.

    The node's TCP API only accepts one client at a time (see firmware
    src/mesh/api/ServerAPI.cpp), so this is the one connection shared by both
    the DB writer and the repeater's client fan-out (repeater_core.py). On a
    hard failure the library's own reader thread exits for good and does not
    reconnect itself, so this class owns reconnect/backoff and swaps `self.iface`
    out from under callers -- always read `reader.iface` fresh, never cache it.
    """

    LOG_DEBUG: Final = 7
    LOG_ERR: Final = 3
    LOG_INFO: Final = 6
    LOG_WARNING: Final = 4

    def __init__(self, hostname, port=4403, stop_event=None):
        self.hostname = hostname
        self.port = port
        self.iface = None
        self._closing = False
        # Also honor the app-wide stop event so a connect-retry loop against an
        # unreachable node can still be interrupted by SIGINT/SIGTERM.
        self._stop_event = stop_event
        self._connected = threading.Event()
        pub.subscribe(self._on_lost, "meshtastic.connection.lost")
        pub.subscribe(self._on_established, "meshtastic.connection.established")
        self._connect_with_backoff()
        self._watchdog = threading.Thread(
            target=self._watch_loop, name="TcpReader watchdog", daemon=True
        )
        self._watchdog.start()

    def _should_stop(self):
        return self._closing or (self._stop_event is not None and not self._stop_event.is_set())

    def _interruptible_sleep(self, seconds):
        """time.sleep() in short steps so a stop request doesn't wait out the
        whole delay (ev_run is cleared, not set, to signal stop, so a plain
        Event.wait() can't be used to detect that)."""
        deadline = time.monotonic() + seconds
        while not self._should_stop() and time.monotonic() < deadline:
            time.sleep(min(0.5, deadline - time.monotonic()))

    def _connect_with_backoff(self):
        delay = BACKOFF_START_SEC
        while not self._should_stop():
            try:
                self.iface = TCPInterface(self.hostname, portNumber=self.port)
                self._connected.set()
                self.log(
                    f"Connected to Meshtastic node at {self.hostname}:{self.port}",
                    level=self.LOG_INFO,
                )
                self.ensure_admin_session_key()
                return
            except Exception as ex:
                self.log(
                    f"Failed connecting to {self.hostname}:{self.port}: {ex}",
                    level=self.LOG_ERR,
                )
                self._interruptible_sleep(delay)
                delay = min(delay * 2, BACKOFF_MAX_SEC)

    def ensure_admin_session_key(self):
        """Bootstrap our own admin session passkey for the local node (fire and
        forget -- the response arrives asynchronously and the library caches it
        itself in iface.nodesByNum). Repeater clients' own admin requests need
        this passkey rewritten in, since their own synthesized handshake never
        goes through this real request/response round trip with the node.
        """
        if self.iface is None:
            return
        try:
            self.iface.localNode.ensureSessionKey()
        except Exception as ex:
            self.log(f"Repeater: failed requesting admin session key: {ex}", level=self.LOG_WARNING)

    def _on_lost(self, interface):
        if interface is self.iface:
            self._connected.clear()

    def _on_established(self, interface):
        if interface is self.iface:
            self._connected.set()

    def _watch_loop(self):
        while not self._should_stop():
            time.sleep(1)
            if self.iface is None or self._connected.is_set():
                continue
            self._interruptible_sleep(RECONNECT_GRACE_SEC)  # let an in-place device resync heal itself
            if self._should_stop() or self._connected.is_set():
                continue
            self.log("Upstream connection lost, reconnecting...", level=self.LOG_WARNING)
            try:
                self.iface.close()
            except Exception:
                pass
            self._connect_with_backoff()

    def is_open(self):
        return self.iface is not None and self._connected.is_set()

    def close(self):
        self._closing = True
        pub.unsubscribe(self._on_lost, "meshtastic.connection.lost")
        pub.unsubscribe(self._on_established, "meshtastic.connection.established")
        if self.iface is not None:
            self.iface.close()

    def log(self, message, level=LOG_INFO):
        """Log a message to stdout. Same ANSI-colored style as the other readers."""
        match level:
            case self.LOG_DEBUG:
                print(f"\x1b[2;37;49m{message}\x1b[0m")
            case self.LOG_ERR:
                print(f"\x1b[0;31;49m{message}\x1b[0m")
            case self.LOG_INFO:
                print(message)
            case self.LOG_WARNING:
                print(f"\x1b[0;33;49m{message}\x1b[0m")
