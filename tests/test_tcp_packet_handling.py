"""Regression tests for the packet -> DB-write logic used by tcpListener().

Packets are synthetic dicts shaped like what the meshtastic library's
"meshtastic.receive" pypubsub callback actually hands over (MessageToDict
output: camelCase keys, portnum as its enum name string). No real TCP
connection is involved -- _handle_tcp_packet() is exercised directly against
an in-memory copy of the real schema.
"""

import sqlite3
import threading

import meshtastic_observer as mo


def make_db():
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE links (
            source INTEGER NOT NULL, destination INTEGER NOT NULL,
            snr REAL DEFAULT -500, seen INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(source, destination)
        );
        CREATE TABLE nodes (
            id INTEGER NOT NULL, shortname TEXT, longname TEXT, seen INTEGER,
            latitude REAL, longitude REAL, tracestart INTEGER DEFAULT 0,
            role INTEGER DEFAULT 0, hardware INTEGER DEFAULT 0, PRIMARY KEY(id)
        );
        CREATE TABLE packets (source INTEGER, type INTEGER, time INTEGER);
        """
    )
    return db


class FakeReader:
    """Stands in for TcpReader: exposes .iface.myInfo.my_node_num and .log()."""

    def __init__(self, own_node_num=None):
        self.iface = None
        if own_node_num is not None:
            self.iface = type("Iface", (), {})()
            self.iface.myInfo = type("MyInfo", (), {"my_node_num": own_node_num})()

    LOG_WARNING = 4

    def log(self, message, level=None):
        pass


def handle(db, packet, own_node_num=None):
    lock = threading.Lock()
    # Same pre-seeded shape as globals.py's module_count (the real code uses
    # module_count[key] += 1, not .get(), for the counters it always touches).
    module_count = {
        "decoded": 0,
        "encrypted": 0,
        "nodeinfo": 0,
        "position": 0,
        "traceroute": 0,
        "text msg": 0,
        "waypoint msg": 0,
        "admin": 0,
    }
    reader = FakeReader(own_node_num)
    mo._handle_tcp_packet(db, lock, module_count, reader, packet)
    return module_count


class TestClassifyTelemetry:
    def test_device_metrics(self):
        assert mo._classify_telemetry({"deviceMetrics": {"batteryLevel": 80}}) == (512, "DeviceTelemetry")

    def test_environment_metrics(self):
        assert mo._classify_telemetry({"environmentMetrics": {"temperature": 21.5}}) == (
            514,
            "EnvironmentTelemetry",
        )

    def test_power_metrics(self):
        assert mo._classify_telemetry({"powerMetrics": {"ch1Voltage": 19.5}}) == (513, "PowerTelemetry")

    def test_unknown_falls_back_to_generic_telemetry(self):
        assert mo._classify_telemetry({}) == (67, "telemetry")


class TestEnumInt:
    def test_known_role(self):
        assert mo._enum_int(mo.config_pb2.Config.DeviceConfig.Role, "ROUTER") == 2

    def test_known_hardware(self):
        assert mo._enum_int(mo.mesh_pb2.HardwareModel, "HELTEC_V3") == 43

    def test_missing_name_uses_default(self):
        assert mo._enum_int(mo.mesh_pb2.HardwareModel, None) == 0

    def test_unknown_name_uses_default(self):
        assert mo._enum_int(mo.mesh_pb2.HardwareModel, "NOT_A_REAL_MODEL") == 0


class TestHandleTcpPacket:
    def test_nodeinfo_upserts_node_with_int_role_and_hardware(self):
        db = make_db()
        packet = {
            "from": 0x6D91908F,
            "decoded": {
                "portnum": "NODEINFO_APP",
                "user": {
                    "shortName": "ABCD",
                    "longName": "Test Node",
                    "role": "ROUTER",
                    "hwModel": "HELTEC_V3",
                },
            },
        }
        handle(db, packet)
        row = db.execute(
            "SELECT shortname, longname, role, hardware FROM nodes WHERE id = ?", (0x6D91908F,)
        ).fetchone()
        assert row == ("ABCD", "Test Node", 2, 43)

    def test_position_updates_lat_lon(self):
        db = make_db()
        db.execute("INSERT INTO nodes VALUES (?, NULL, NULL, 0, NULL, NULL, 0, 0, 0)", (1,))
        packet = {
            "from": 1,
            "decoded": {
                "portnum": "POSITION_APP",
                "position": {"latitudeI": 500000000, "longitudeI": 100000000},
            },
        }
        handle(db, packet)
        row = db.execute("SELECT latitude, longitude FROM nodes WHERE id = 1").fetchone()
        assert row == (50.0, 10.0)

    def test_zero_position_is_ignored(self):
        db = make_db()
        db.execute("INSERT INTO nodes VALUES (?, NULL, NULL, 0, 12.0, 34.0, 0, 0, 0)", (1,))
        packet = {
            "from": 1,
            "decoded": {"portnum": "POSITION_APP", "position": {"latitudeI": 0, "longitudeI": 0}},
        }
        handle(db, packet)
        row = db.execute("SELECT latitude, longitude FROM nodes WHERE id = 1").fetchone()
        assert row == (12.0, 34.0)  # unchanged

    def test_telemetry_inserts_packet_with_synthetic_port(self):
        db = make_db()
        packet = {
            "from": 1,
            "decoded": {"portnum": "TELEMETRY_APP", "telemetry": {"deviceMetrics": {"batteryLevel": 90}}},
        }
        handle(db, packet)
        row = db.execute("SELECT source, type FROM packets").fetchone()
        assert row == (1, 512)

    def test_own_node_packet_skips_packets_table_but_updates_node(self):
        db = make_db()
        packet = {
            "from": 42,
            "decoded": {
                "portnum": "NODEINFO_APP",
                "user": {"shortName": "ME", "longName": "Gateway", "role": "CLIENT", "hwModel": "UNSET"},
            },
        }
        handle(db, packet, own_node_num=42)
        assert db.execute("SELECT count(*) FROM packets").fetchone()[0] == 0
        row = db.execute("SELECT shortname FROM nodes WHERE id = 42").fetchone()
        assert row == ("ME",)

    def test_traceroute_writes_full_hop_chain_not_just_endpoints(self):
        db = make_db()
        packet = {
            "from": 1,
            "to": 4,
            "decoded": {
                "portnum": "TRACEROUTE_APP",
                "traceroute": {"route": [2, 3], "snrTowards": [40, 20, 8]},
            },
        }
        handle(db, packet)
        links = sorted(db.execute("SELECT source, destination, snr FROM links").fetchall())
        assert links == [(1, 2, 10.0), (2, 3, 5.0), (3, 4, 2.0)]

    def test_undecoded_packet_counts_as_encrypted_and_is_dropped(self):
        db = make_db()
        counts = handle(db, {"from": 1})
        assert counts["encrypted"] == 1
        assert db.execute("SELECT count(*) FROM packets").fetchone()[0] == 0
