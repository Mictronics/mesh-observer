"""Regression tests for mesh_issues.py's detection functions, against an
in-memory copy of the real schema (same pattern as test_tcp_packet_handling.py)."""

import base64
import hashlib
import sqlite3
import time

import mesh_issues


def make_db():
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE nodes (
            id INTEGER NOT NULL, shortname TEXT, longname TEXT, seen INTEGER,
            latitude REAL, longitude REAL,
            role INTEGER DEFAULT 0, hardware INTEGER DEFAULT 0,
            public_key TEXT, PRIMARY KEY(id)
        );
        CREATE TABLE packets (
            source INTEGER, type INTEGER, time INTEGER,
            hops_used INTEGER, rx_snr REAL, rx_rssi INTEGER,
            channel_util REAL, air_util_tx REAL, hop_start INTEGER
        );
        CREATE TABLE link_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source INTEGER NOT NULL, destination INTEGER NOT NULL,
            snr REAL, trace_id INTEGER, seen INTEGER NOT NULL
        );
        CREATE TABLE security_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL, node_id INTEGER, detail TEXT, seen INTEGER NOT NULL
        );
        """
    )
    return db


def test_stale_nodes_flags_old_seen():
    db = make_db()
    db.execute(
        "INSERT INTO nodes VALUES (1, NULL, 'Old', ?, NULL, NULL, 0, 0, NULL)", (int(time.time()) - 90000,)
    )
    findings = mesh_issues._stale_nodes(db.cursor(), {1: "Old"})
    assert [f["node_id"] for f in findings] == [1]


def test_stale_nodes_ignores_nodes_unseen_beyond_max_window():
    db = make_db()
    db.execute(
        "INSERT INTO nodes VALUES (1, NULL, 'Ancient', ?, NULL, NULL, 0, 0, NULL)",
        (int(time.time()) - mesh_issues.STALE_NODE_MAX_SECONDS - 3600,),
    )
    findings = mesh_issues._stale_nodes(db.cursor(), {1: "Ancient"})
    assert findings == []


def test_chatty_nodes_flags_high_airtime():
    db = make_db()
    for _ in range(6):
        db.execute("INSERT INTO packets (source, air_util_tx) VALUES (1, 10.0)")
    findings = mesh_issues._chatty_nodes(db.cursor(), {1: "A"})
    assert [f["node_id"] for f in findings] == [1]


def test_congested_area_flags_dense_high_util_bin():
    db = make_db()
    for node_id in (1, 2, 3):
        db.execute("INSERT INTO nodes VALUES (?, NULL, NULL, 0, 48.0, 10.0, 0, 0, NULL)", (node_id,))
        db.execute("INSERT INTO packets (source, channel_util) VALUES (?, 30.0)", (node_id,))
    findings = mesh_issues._congested_areas(db.cursor(), {1: "A", 2: "B", 3: "C"})
    assert len(findings) == 1


def test_router_cluster_flags_mutually_adjacent_routers():
    db = make_db()
    for node_id in (1, 2):
        db.execute("INSERT INTO nodes VALUES (?, NULL, NULL, 0, NULL, NULL, 2, 0, NULL)", (node_id,))
    db.execute("INSERT INTO link_history (source, destination, snr, trace_id, seen) VALUES (1, 2, 5.0, 1, 0)")
    cur = db.cursor()
    node_info = mesh_issues._load_node_info(cur)
    graph, _, _, _ = mesh_issues._load_link_graph(cur)
    findings = mesh_issues._router_clusters(graph, node_info, {1: "R1", 2: "R2"})
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"


def test_asymmetric_link_flags_snr_delta():
    db = make_db()
    for snr in (10, 11, 9):
        db.execute(
            "INSERT INTO link_history (source, destination, snr, trace_id, seen) VALUES (1, 2, ?, 1, 0)", (snr,)
        )
    for snr in (-5, -4, -6):
        db.execute(
            "INSERT INTO link_history (source, destination, snr, trace_id, seen) VALUES (2, 1, ?, 2, 0)", (snr,)
        )
    cur = db.cursor()
    _, directed_snr, _, _ = mesh_issues._load_link_graph(cur)
    findings = mesh_issues._asymmetric_links(directed_snr, {1: "A", 2: "B"})
    assert len(findings) == 1


def test_hop_horizon_flags_exhausted_budget():
    db = make_db()
    for _ in range(15):
        db.execute("INSERT INTO packets (source, hops_used, hop_start) VALUES (1, 3, 3)")
    for _ in range(5):
        db.execute("INSERT INTO packets (source, hops_used, hop_start) VALUES (1, 1, 3)")
    findings = mesh_issues._hop_horizon(db.cursor(), {1: "A"})
    assert [f["node_id"] for f in findings] == [1]


def test_coverage_shadow_flags_never_heard_directly():
    db = make_db()
    db.execute("INSERT INTO nodes VALUES (1, NULL, NULL, 0, 48.0, 10.0, 0, 0, NULL)")
    for _ in range(3):
        db.execute("INSERT INTO packets (source, hops_used) VALUES (1, 2)")
    findings = mesh_issues._coverage_shadow(db.cursor(), {1: "A"})
    assert [f["node_id"] for f in findings] == [1]


def test_duplicate_keys_flags_shared_key():
    db = make_db()
    db.execute("INSERT INTO nodes VALUES (1, NULL, NULL, 0, NULL, NULL, 0, 0, 'KEY')")
    db.execute("INSERT INTO nodes VALUES (2, NULL, NULL, 0, NULL, NULL, 0, 0, 'KEY')")
    findings = mesh_issues._duplicate_keys(db.cursor(), {1: "A", 2: "B"})
    assert len(findings) == 1


def test_low_entropy_key_flags_blacklisted_hash():
    raw = bytes([1]) * 32  # meshmonitor's documented dev/test key
    assert hashlib.sha256(raw).hexdigest() in mesh_issues.LOW_ENTROPY_KEY_HASHES
    db = make_db()
    db.execute("INSERT INTO nodes VALUES (1, NULL, NULL, 0, NULL, NULL, 0, 0, ?)", (base64.b64encode(raw).decode(),))
    findings = mesh_issues._low_entropy_keys(db.cursor(), {1: "A"})
    assert [f["node_id"] for f in findings] == [1]


def test_key_mismatches_reads_security_events():
    db = make_db()
    db.execute("INSERT INTO security_events (kind, node_id, detail, seen) VALUES ('key_mismatch', 1, 'old -> new', 0)")
    findings = mesh_issues._key_mismatches(db.cursor(), {1: "A"})
    assert len(findings) == 1
    assert findings[0]["severity"] == "critical"


def test_spoofed_packets_reads_security_events():
    db = make_db()
    db.execute("INSERT INTO security_events (kind, node_id, detail, seen) VALUES ('spoofed_packet', 1, 'id 0x1', 0)")
    findings = mesh_issues._spoofed_packets(db.cursor(), {1: "A"})
    assert len(findings) == 1
    assert findings[0]["severity"] == "critical"


def test_excessive_packet_rate_flags_high_volume():
    db = make_db()
    now = int(time.time())
    for _ in range(31):
        db.execute("INSERT INTO packets (source, time) VALUES (1, ?)", (now,))
    findings = mesh_issues._excessive_packet_rate(db.cursor(), {1: "A"})
    assert [f["node_id"] for f in findings] == [1]


def test_analyze_combines_and_sorts_critical_first():
    db = make_db()
    now = int(time.time())
    db.execute("INSERT INTO nodes VALUES (1, NULL, NULL, ?, NULL, NULL, 0, 0, NULL)", (now - 90000,))  # stale (info)
    db.execute("INSERT INTO nodes VALUES (2, NULL, NULL, ?, NULL, NULL, 0, 0, 'KEY')", (now,))  # duplicate (critical)
    db.execute("INSERT INTO nodes VALUES (3, NULL, NULL, ?, NULL, NULL, 0, 0, 'KEY')", (now,))
    db.commit()
    findings = mesh_issues.analyze(db)
    assert findings[0]["severity"] == "critical"
    assert any(f["category"] == "stale" for f in findings)
