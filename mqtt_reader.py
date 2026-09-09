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
import json
import threading
from typing import Final

import paho.mqtt.client as mqtt
from pubsub import pub

CONNECT_TIMEOUT = 5  # seconds to wait for the async CONNACK before giving up


class MqttReader:
    """Subscribes to a Meshtastic MQTT JSON topic and publishes each decoded
    message via pypubsub. Unlike JournalReader/SerialReader this is
    callback-driven (paho runs its own network thread), so it does not
    implement poll_read() and is not used by logParser().
    """

    LOG_DEBUG: Final = 7
    LOG_ERR: Final = 3
    LOG_INFO: Final = 6
    LOG_NOTICE: Final = 5
    LOG_WARNING: Final = 4

    def __init__(self, broker, port, username, password, topic):
        self.topic = topic
        self._connected = False
        # on_connect/on_disconnect fire asynchronously on paho's own thread
        # (started by loop_start() below); is_open() would otherwise race
        # against that callback and report False before it ever runs.
        self._connect_event = threading.Event()
        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(username, password)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        try:
            self.client.connect(broker, port, keepalive=60)
            self.client.loop_start()
            if not self._connect_event.wait(timeout=CONNECT_TIMEOUT):
                self.log(
                    f"Timed out waiting {CONNECT_TIMEOUT}s for MQTT broker connection.",
                    level=self.LOG_ERR,
                )
        except OSError as ex:
            self.log(f"Failed connecting to MQTT broker. Error was: {ex}", level=self.LOG_ERR)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self._connected = True
            client.subscribe(self.topic)
            self.log(f"Connected to MQTT broker, subscribed to {self.topic}", level=self.LOG_INFO)
        else:
            self.log(f"MQTT connect failed: {reason_code}", level=self.LOG_ERR)
        self._connect_event.set()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._connected = False
        self.log(f"MQTT disconnected: {reason_code}", level=self.LOG_WARNING)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            # Some topics (e.g. .../state) carry plain text, not JSON; ignore those.
            return
        pub.sendMessage("mqtt.message", topic=msg.topic, payload=payload)

    def is_open(self):
        return self._connected

    def close(self):
        if self._connected:
            self.client.loop_stop()
            self.client.disconnect()

    def log(self, message, level=LOG_INFO):
        """Log a message to stdout. Same ANSI-colored style as SerialReader."""
        match level:
            case self.LOG_DEBUG:
                print(f"\x1b[2;37;49m{message}\x1b[0m")
            case self.LOG_ERR:
                print(f"\x1b[0;31;49m{message}\x1b[0m")
            case self.LOG_INFO:
                print(message)
            case self.LOG_NOTICE:
                print(f"\x1b[0;36;49m{message}\x1b[0m")
            case self.LOG_WARNING:
                print(f"\x1b[0;33;49m{message}\x1b[0m")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
