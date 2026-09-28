#!python3

# This file is part of Meshtastic mesh observer.
#
# Copyright (c) 2026 Michael Wolf <michael@mictronics.de>
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
"""Mesh health/topology/security analysis, conceptually ported from
github.com/Yeraze/meshmonitor's mesh-issues rule engine (TypeScript) onto
this project's SQLite schema -- read-only against the tables
meshtastic_observer.py already writes (nodes, packets, link_history,
security_events). No capture code, no thread; analyze() is called from
statistics() on the existing daily schedule.
"""
import base64
import hashlib
import math
from collections import defaultdict

import networkx as nx

STALE_NODE_SECONDS = 86400
# Upper bound matches the 7-day window statistics() already uses elsewhere
# (packets' own retention) -- without it, nodes (which has no retention
# trigger, unlike links/packets) would flag every node ever seen, not just
# ones that went quiet recently.
STALE_NODE_MAX_SECONDS = 7 * 86400
CHATTY_AIRTIME_PCT = 8.0
CHATTY_MIN_SAMPLES = 6
CONGESTION_GRID_DEG = 0.05  # ~5.5km at mid latitudes
CONGESTION_MIN_NODES = 3
CONGESTION_CHANNEL_UTIL_PCT = 25.0
ROUTER_ROLES = (2, 3, 4)  # Config.DeviceConfig.Role: Router, Router Client, Repeater
ROUTER_CLUSTER_WARN_SIZE = 2
ROUTER_CLUSTER_CRIT_SIZE = 4
ROUTER_EDGE_MAX_KM = 30
REDUNDANT_MIN_NEIGHBORS = 3
REDUNDANT_OVERLAP_PCT = 90.0
REDUNDANT_PROXIMITY_KM = 10
ASYMMETRIC_SNR_DELTA_DB = 6.0
ASYMMETRIC_MIN_SAMPLES = 3
IDLE_ROUTER_MIN_PATHS = 20
IDLE_ROUTER_MAX_HOPSHARE_PCT = 1.0
LOADBEARING_MIN_TRACEROUTES = 10
LOADBEARING_MIN_PATHSHARE_PCT = 25.0
HOP_HORIZON_MIN_PACKETS = 20
HOP_HORIZON_EXHAUSTED_PCT = 50.0
COVERAGE_SHADOW_MIN_SAMPLES = 3
EXCESSIVE_PACKETS_PER_HOUR = 30

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# SHA-256 hashes of public keys known to be generated with insufficient
# firmware randomness. Ported verbatim from meshmonitor's
# lowEntropyKeyService.ts (github.com/Yeraze/meshmonitor) -- 3 of the 25
# entries there are shorter than 64 hex chars in the upstream source itself
# and can never match a real SHA-256 digest; kept as-is for parity, they're
# simply inert, same as upstream.
LOW_ENTROPY_KEY_HASHES = {
    "72cd6e8422c407fb6d098690f1130b7ded7ec2f7f5e1d30bd9d521f015363793",
    "f47ecc17e6b4a322eceed9084f3963ea8075e124ce053669633b2cbc028d348b",
    "5a9ea2a68aa666c15f550064a3a6fe71c0bb82c3323d7a7ae36efddda3a66b9",
    "b3df3b2e67b6d5f8df762c455e2ebd16c5f867aa15f8920bdf5a6650ac0dbb2f",
    "3b8f863a381f7739a94eef91185a62e1aa9d36eace60358d9d1ff4b8c9136a5d",
    "367e2de1845f4252290a256454a6bfdb665ff151a51712240757f6919b6458",
    "1677eba45291fb26cf8fd7d9d15dc4687375edc55558ee9056d42f3129f78c1f",
    "318ca95eed3c12bf979c478e989dc23e86239029c8b020f8b1b0aa192acf0a54",
    "a48a990e51dc1220f313f52b3ae24342c65298cdbbcab131a0d4d630f327fb49",
    "d23f138d22048d075958a0f955cf30a02e2fca8020e4dea1add958b3432b2270",
    "4041ec6ad2d603e49a9ebd6c0a9b75a4bcab6fa795ff2df6e9b9ab4c0c1cd03b",
    "2249322b00f922fa1702e96482f04d1bc704fcdc8c5eb6d916d637ce59aa0949",
    "486f1e48978864ace8eb30a3c3e1cf9739a6555b5fbf18b73adfa875e79de01e",
    "09b4e26d2898c9476646bfff581791aac3bf4a9d0b88b1f103dd61d7ba9e6498",
    "393984e0222f7d78451872b413d2012f3ca1b0fe39d0f13c72d6ef54d57722a0",
    "0ada5fecff5cc02e5fc48d03e58059d35d4986e98df6f616353df99b29559e64",
    "0856f0d7ef77d6118c952d3cdfb122bf609be5a9c06e4b01dcd15744b2a5cf",
    "2cb27785d6b7489cfebc802660f46dce1131a21e330a6d2b00fa0c90958f5c6b",
    "fa59c86e94ee75c99ab0fe893640c9994a3bf4aa1224a20ff9d108cb7819aae5",
    "6e427a4a8c616222a189d3a4c219a38353a77a0a89e2545262e7ca8cf66a60",
    "20272fba0c99d729f31135899d0e24a1c3cbdf8af1c6fed0d79f92d68f59bfe4",
    "9170b47cfbffa0596a251ca99ee943815d74b1b10928004aafe3fca94e27764c",
    "85fe7cecb67874c3ece1327fb0b70274f923d8e7fa14e6ee6644b18ca52f7ed2",
    "8e66657b3b6f7ecc57b457eacc83f5aaf765a3ce937213c1b6467b2945b5c893",
    "cc11fb1aaba131876ac6de8887a9b9593782d8b2ccd897409a5c8f4055cb4c3e",
}


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two lat/lon points, in kilometers."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _finding(severity, category, message, node_id=None):
    return {"severity": severity, "category": category, "message": message, "node_id": node_id}


def _label(names, node_id):
    return names.get(node_id, f"{node_id:08X}")


def _area_key(node_info, node_id):
    info = node_info.get(node_id, {})
    lat, lon = info.get("lat"), info.get("lon")
    if lat is None or lon is None:
        return None
    return (math.floor(lat / CONGESTION_GRID_DEG), math.floor(lon / CONGESTION_GRID_DEG))


def _load_node_info(cur):
    rows = cur.execute("SELECT id, role, latitude, longitude FROM nodes").fetchall()
    return {node_id: {"role": role, "lat": lat, "lon": lon} for node_id, role, lat, lon in rows}


def _load_link_graph(cur):
    """Undirected adjacency graph + per-directed-edge SNR samples + per-trace
    node/edge membership, all derived from link_history (one row per
    traceroute-observed hop, see meshtastic_observer.py's TRACEROUTE_APP
    handling)."""
    rows = cur.execute("SELECT source, destination, snr, trace_id FROM link_history").fetchall()
    graph = nx.Graph()
    directed_snr = defaultdict(list)
    paths = defaultdict(set)  # trace_id -> {node_id, ...}
    path_edges = defaultdict(list)  # trace_id -> [(source, destination), ...]
    for source, destination, snr, trace_id in rows:
        graph.add_edge(source, destination)
        if snr is not None and snr != -500:
            directed_snr[(source, destination)].append(snr)
        if trace_id is not None:
            paths[trace_id].update((source, destination))
            path_edges[trace_id].append((source, destination))
    return graph, directed_snr, paths, path_edges


def _stale_nodes(cur, names):
    rows = cur.execute(
        "SELECT id, (strftime('%s','now') - seen) / 3600.0 FROM nodes "
        "WHERE seen IS NOT NULL AND (strftime('%s','now') - seen) BETWEEN ? AND ?",
        (STALE_NODE_SECONDS, STALE_NODE_MAX_SECONDS),
    ).fetchall()
    return [
        _finding("info", "stale", f"{_label(names, node_id)} seit {hours:.0f}h nicht mehr gehört.", node_id)
        for node_id, hours in rows
    ]


def _chatty_nodes(cur, names):
    rows = cur.execute(
        "SELECT source, AVG(air_util_tx), COUNT(*) FROM packets "
        "WHERE air_util_tx IS NOT NULL GROUP BY source "
        "HAVING AVG(air_util_tx) > ? AND COUNT(*) >= ?",
        (CHATTY_AIRTIME_PCT, CHATTY_MIN_SAMPLES),
    ).fetchall()
    return [
        _finding(
            "warning",
            "traffic",
            f"{_label(names, source)}: mittlere Kanalbelegung (Airtime TX) {avg:.1f}% über {count} Aussendungen.",
            source,
        )
        for source, avg, count in rows
    ]


def _congested_areas(cur, names):
    node_util = dict(
        cur.execute(
            "SELECT source, AVG(channel_util) FROM packets WHERE channel_util IS NOT NULL GROUP BY source"
        ).fetchall()
    )
    positions = cur.execute(
        "SELECT id, latitude, longitude FROM nodes WHERE latitude IS NOT NULL AND longitude IS NOT NULL "
        "AND NOT (latitude = 0 AND longitude = 0)"
    ).fetchall()
    bins = defaultdict(list)
    for node_id, lat, lon in positions:
        if node_id not in node_util:
            continue
        key = (math.floor(lat / CONGESTION_GRID_DEG), math.floor(lon / CONGESTION_GRID_DEG))
        bins[key].append((node_id, node_util[node_id]))
    findings = []
    for members in bins.values():
        if len(members) < CONGESTION_MIN_NODES:
            continue
        mean_util = sum(u for _, u in members) / len(members)
        if mean_util > CONGESTION_CHANNEL_UTIL_PCT:
            labels = ", ".join(_label(names, n) for n, _ in members)
            findings.append(
                _finding(
                    "warning",
                    "traffic",
                    f"Überlastetes Gebiet: {len(members)} Knoten ({labels}) mit mittlerer Kanalauslastung {mean_util:.1f}%.",
                )
            )
    return findings


def _router_clusters(graph, node_info, names):
    router_nodes = [n for n in graph.nodes if node_info.get(n, {}).get("role") in ROUTER_ROLES]
    sub = graph.subgraph(router_nodes).copy()
    for u, v in list(sub.edges):
        iu, iv = node_info.get(u, {}), node_info.get(v, {})
        if iu.get("lat") is not None and iv.get("lat") is not None:
            if haversine_km(iu["lat"], iu["lon"], iv["lat"], iv["lon"]) > ROUTER_EDGE_MAX_KM:
                sub.remove_edge(u, v)
    findings = []
    for component in nx.connected_components(sub):
        if len(component) < ROUTER_CLUSTER_WARN_SIZE:
            continue
        severity = "critical" if len(component) >= ROUTER_CLUSTER_CRIT_SIZE else "warning"
        labels = ", ".join(_label(names, n) for n in component)
        findings.append(
            _finding(
                severity,
                "topology",
                f"Router-Cluster mit {len(component)} sich gegenseitig hörenden Routern: {labels}.",
            )
        )
    return findings


def _redundant_routers(graph, node_info, names):
    findings = []
    router_nodes = [n for n in graph.nodes if node_info.get(n, {}).get("role") in ROUTER_ROLES]
    for a in router_nodes:
        neighbors_a = set(graph.neighbors(a))
        if len(neighbors_a) < REDUNDANT_MIN_NEIGHBORS:
            continue
        for b in router_nodes:
            if a == b:
                continue
            neighbors_b = set(graph.neighbors(b))
            if len(neighbors_b) <= len(neighbors_a):
                continue
            ia, ib = node_info.get(a, {}), node_info.get(b, {})
            if ia.get("lat") is not None and ib.get("lat") is not None:
                if haversine_km(ia["lat"], ia["lon"], ib["lat"], ib["lon"]) > REDUNDANT_PROXIMITY_KM:
                    continue
            overlap = len(neighbors_a & neighbors_b) / len(neighbors_a) * 100
            if overlap >= REDUNDANT_OVERLAP_PCT:
                findings.append(
                    _finding(
                        "warning",
                        "topology",
                        f"{_label(names, a)} ist redundant: {overlap:.0f}% seiner Nachbarn werden bereits "
                        f"von {_label(names, b)} abgedeckt.",
                        a,
                    )
                )
                break
    return findings


def _asymmetric_links(directed_snr, names):
    findings = []
    seen_pairs = set()
    for (a, b), snr_ab in directed_snr.items():
        pair = frozenset((a, b))
        if pair in seen_pairs:
            continue
        snr_ba = directed_snr.get((b, a), [])
        if len(snr_ab) < ASYMMETRIC_MIN_SAMPLES or len(snr_ba) < ASYMMETRIC_MIN_SAMPLES:
            continue
        seen_pairs.add(pair)
        mean_ab = sum(snr_ab) / len(snr_ab)
        mean_ba = sum(snr_ba) / len(snr_ba)
        if abs(mean_ab - mean_ba) > ASYMMETRIC_SNR_DELTA_DB:
            findings.append(
                _finding(
                    "warning",
                    "topology",
                    f"Asymmetrische Verbindung {_label(names, a)} <-> {_label(names, b)}: "
                    f"{mean_ab:.1f} dB vs {mean_ba:.1f} dB.",
                )
            )
    return findings


def _intermediate_trace_ids(path_edges, node_id):
    result = set()
    for trace_id, edges in path_edges.items():
        as_dst = any(dst == node_id for _, dst in edges)
        as_src = any(src == node_id for src, _ in edges)
        if as_dst and as_src:
            result.add(trace_id)
    return result


def _idle_and_loadbearing_nodes(graph, node_info, paths, path_edges, names):
    path_areas = {}
    for trace_id, members in paths.items():
        areas = {_area_key(node_info, n) for n in members}
        areas.discard(None)
        path_areas[trace_id] = areas

    idle_findings = []
    loadbearing_findings = []
    for node_id in graph.nodes:
        area = _area_key(node_info, node_id)
        if area is None:
            continue
        in_area_paths = {tid for tid, areas in path_areas.items() if area in areas}
        total_area_paths = len(in_area_paths)
        if total_area_paths == 0:
            continue
        node_trace_ids = {tid for tid, edges in path_edges.items() if any(node_id in e for e in edges)}
        hop_share_pct = len(node_trace_ids & in_area_paths) / total_area_paths * 100
        role = node_info.get(node_id, {}).get("role")

        if (
            role in ROUTER_ROLES
            and graph.degree[node_id] > 0
            and total_area_paths >= IDLE_ROUTER_MIN_PATHS
            and hop_share_pct < IDLE_ROUTER_MAX_HOPSHARE_PCT
        ):
            idle_findings.append(
                _finding(
                    "warning",
                    "topology",
                    f"{_label(names, node_id)} ist ein Router mit Nachbarn, trägt aber nur {hop_share_pct:.1f}% "
                    f"der {total_area_paths} Traceroute-Pfade im Gebiet.",
                    node_id,
                )
            )

        if role not in ROUTER_ROLES:
            intermediate_ids = _intermediate_trace_ids(path_edges, node_id)
            if len(intermediate_ids) >= LOADBEARING_MIN_TRACEROUTES and hop_share_pct >= LOADBEARING_MIN_PATHSHARE_PCT:
                loadbearing_findings.append(
                    _finding(
                        "warning",
                        "topology",
                        f"{_label(names, node_id)} ist ein normaler Knoten, trägt aber {hop_share_pct:.1f}% "
                        f"der Traceroute-Pfade im Gebiet ({len(intermediate_ids)}x als Zwischenstation).",
                        node_id,
                    )
                )
    return idle_findings, loadbearing_findings


def _hop_horizon(cur, names):
    rows = cur.execute(
        "SELECT source, SUM(CASE WHEN hops_used = hop_start THEN 1 ELSE 0 END), COUNT(*) "
        "FROM packets WHERE hop_start IS NOT NULL AND hops_used IS NOT NULL "
        "GROUP BY source HAVING COUNT(*) >= ?",
        (HOP_HORIZON_MIN_PACKETS,),
    ).fetchall()
    findings = []
    for source, exhausted, total in rows:
        pct = exhausted / total * 100
        if pct > HOP_HORIZON_EXHAUSTED_PCT:
            findings.append(
                _finding(
                    "warning",
                    "topology",
                    f"{_label(names, source)}: {pct:.0f}% der Pakete erreichen das Hop-Limit "
                    f"(erschöpftes Hop-Budget bei {total} Paketen).",
                    source,
                )
            )
    return findings


def _coverage_shadow(cur, names):
    # No MQTT ingestion path exists in this project (TCP-only, see
    # CLAUDE.md), so meshmonitor's MQTT-vs-RF sourcing check has no analog
    # here -- reinterpreted as "never once heard directly" instead.
    rows = cur.execute(
        "SELECT p.source, COUNT(*), SUM(CASE WHEN p.hops_used = 0 THEN 1 ELSE 0 END) "
        "FROM packets p JOIN nodes n ON n.id = p.source "
        "WHERE p.hops_used IS NOT NULL AND n.latitude IS NOT NULL AND n.longitude IS NOT NULL "
        "AND NOT (n.latitude = 0 AND n.longitude = 0) "
        "GROUP BY p.source HAVING COUNT(*) >= ?",
        (COVERAGE_SHADOW_MIN_SAMPLES,),
    ).fetchall()
    return [
        _finding(
            "info",
            "topology",
            f"{_label(names, source)} wurde nie direkt (0 Hops) empfangen -- nur über Relais erreichbar "
            f"({total} Aussendungen im Zeitraum).",
            source,
        )
        for source, total, direct in rows
        if direct == 0
    ]


def _duplicate_keys(cur, names):
    rows = cur.execute(
        "SELECT public_key, GROUP_CONCAT(id) FROM nodes "
        "WHERE public_key IS NOT NULL GROUP BY public_key HAVING COUNT(*) > 1"
    ).fetchall()
    findings = []
    for _public_key, ids_csv in rows:
        ids = [int(x) for x in ids_csv.split(",")]
        labels = ", ".join(_label(names, i) for i in ids)
        findings.append(
            _finding("critical", "security", f"Doppelter öffentlicher Schlüssel bei mehreren Knoten: {labels}.")
        )
    return findings


def _low_entropy_keys(cur, names):
    findings = []
    for node_id, public_key in cur.execute("SELECT id, public_key FROM nodes WHERE public_key IS NOT NULL"):
        try:
            raw = base64.b64decode(public_key)
        except Exception:
            continue
        if len(raw) != 32:
            continue
        if hashlib.sha256(raw).hexdigest() in LOW_ENTROPY_KEY_HASHES:
            findings.append(
                _finding(
                    "critical",
                    "security",
                    f"{_label(names, node_id)} verwendet einen bekannten Schlüssel mit unzureichender Entropie.",
                    node_id,
                )
            )
    return findings


def _security_events(cur, names, kind, severity, template):
    rows = cur.execute(
        "SELECT node_id, detail FROM security_events WHERE kind = ? ORDER BY seen DESC", (kind,)
    ).fetchall()
    return [
        _finding(severity, "security", template.format(label=_label(names, node_id), detail=detail or ""), node_id)
        for node_id, detail in rows
    ]


def _key_mismatches(cur, names):
    return _security_events(
        cur, names, "key_mismatch", "critical", "{label}: öffentlicher Schlüssel hat sich geändert ({detail})."
    )


def _spoofed_packets(cur, names):
    return _security_events(
        cur,
        names,
        "spoofed_packet",
        "critical",
        "Verdacht auf Identitätsdiebstahl: Paket mit fremder Knoten-ID {label} und unbekannter Paket-ID ({detail}).",
    )


def _excessive_packet_rate(cur, names):
    rows = cur.execute(
        "SELECT source, COUNT(*) FROM packets WHERE time > strftime('%s','now') - 3600 "
        "GROUP BY source HAVING COUNT(*) > ?",
        (EXCESSIVE_PACKETS_PER_HOUR,),
    ).fetchall()
    return [
        _finding(
            "warning",
            "traffic",
            f"{_label(names, source)}: {count} Pakete in der letzten Stunde (>{EXCESSIVE_PACKETS_PER_HOUR}).",
            source,
        )
        for source, count in rows
    ]


def analyze(database):
    """Run every mesh-health/topology/security check against the current DB
    state and return a flat list of findings, most severe first. Read-only;
    caller is expected to already hold the DB lock (see statistics())."""
    cur = database.cursor()
    names = dict(
        cur.execute("SELECT id, coalesce(longname, shortname, printf('%08X', id)) FROM nodes").fetchall()
    )
    node_info = _load_node_info(cur)
    graph, directed_snr, paths, path_edges = _load_link_graph(cur)
    idle_findings, loadbearing_findings = _idle_and_loadbearing_nodes(graph, node_info, paths, path_edges, names)

    findings = []
    findings += _stale_nodes(cur, names)
    findings += _chatty_nodes(cur, names)
    findings += _congested_areas(cur, names)
    findings += _router_clusters(graph, node_info, names)
    findings += _redundant_routers(graph, node_info, names)
    findings += _asymmetric_links(directed_snr, names)
    findings += idle_findings
    findings += loadbearing_findings
    findings += _hop_horizon(cur, names)
    findings += _coverage_shadow(cur, names)
    findings += _duplicate_keys(cur, names)
    findings += _low_entropy_keys(cur, names)
    findings += _key_mismatches(cur, names)
    findings += _spoofed_packets(cur, names)
    findings += _excessive_packet_rate(cur, names)
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 99))
    cur.close()
    return findings
