"""Regression tests for the pure JSON-routing helpers used by mqttListener().

Fixtures are JSON payloads copied verbatim from a real captured MQTT session
(meshtastic_mqtt.log). Only the pure classification/parsing helpers are
tested here -- no real MQTT connection or database is involved.
"""

import sqlite3
import threading

import meshtastic_observer as mo


class TestTopicKind:
    def test_channel_topic(self):
        assert mo.mqtt_topic_kind("msh/2/json/MediumFast/!6d91908f") == "channel"

    def test_device_self_report_topic(self):
        assert mo.mqtt_topic_kind("msh/2/json/6d91908f/device") == "device"

    def test_environment_self_report_topic(self):
        assert mo.mqtt_topic_kind("msh/2/json/6d91908f/environment") == "environment"

    def test_local_stats_self_report_topic(self):
        assert mo.mqtt_topic_kind("msh/2/json/6d91908f/localStats") == "localStats"

    def test_state_topic_is_not_a_self_report(self):
        # These carry plain text, not JSON, and are filtered out before
        # reaching mqtt_topic_kind() (see MqttReader._on_message), but the
        # classifier itself should still fall back to "channel" rather than
        # mistakenly matching a self-report suffix.
        assert mo.mqtt_topic_kind("msh/2/json/private/state") == "channel"


class TestNodeIdParsing:
    def test_bang_prefixed_hex(self):
        assert mo.mqtt_node_id("!6d91908f") == 0x6D91908F

    def test_bare_hex(self):
        assert mo.mqtt_node_id("6d91908f") == 0x6D91908F


class TestTelemetryClassification:
    def test_device_metrics(self):
        payload = {
            "air_util_tx": 0.078,
            "battery_level": 101,
            "channel_utilization": 0,
            "uptime_seconds": 86470,
            "voltage": 4.94,
        }
        assert mo.classify_mqtt_telemetry(payload) == 512

    def test_power_metrics(self):
        payload = {
            "current_ch1": 2.8,
            "current_ch2": 0,
            "current_ch3": 0,
            "voltage_ch1": 19.52,
            "voltage_ch2": 0,
            "voltage_ch3": 0,
        }
        assert mo.classify_mqtt_telemetry(payload) == 513

    def test_environment_metrics(self):
        payload = {
            "barometric_pressure": 962.36,
            "current": -20.3,
            "gas_resistance": 257.38,
            "iaq": 171,
            "relative_humidity": 63.43,
            "temperature": 22.06,
            "voltage": 4.088,
        }
        assert mo.classify_mqtt_telemetry(payload) == 514

    def test_unrecognized_payload_falls_back_to_generic_telemetry(self):
        # Real sample: a distance-sensor-like payload with no matching keys.
        assert mo.classify_mqtt_telemetry({"distance": 4500}) == 67


class TestSelfReportOwnNodeFilter:
    def _run(self, topic, own_node_id):
        database = sqlite3.connect(":memory:")
        database.execute("CREATE TABLE packets (source INTEGER, type INTEGER, time INTEGER)")
        counts = {}
        mo._handle_self_report(
            database, threading.Lock(), counts, topic, 512, "DeviceTelemetry", own_node_id
        )
        rows = database.execute("SELECT source FROM packets").fetchall()
        database.close()
        return counts, rows

    def test_own_node_self_report_is_dropped(self):
        counts, rows = self._run("msh/2/json/6d91908f/device", mo.mqtt_node_id("6d91908f"))
        assert rows == []
        assert counts == {}

    def test_other_node_self_report_is_kept(self):
        counts, rows = self._run("msh/2/json/17819f35/device", mo.mqtt_node_id("6d91908f"))
        assert counts == {"DeviceTelemetry": 1}
        assert len(rows) == 1

    def test_no_own_node_configured_keeps_everything(self):
        counts, rows = self._run("msh/2/json/6d91908f/device", None)
        assert counts == {"DeviceTelemetry": 1}
        assert len(rows) == 1
