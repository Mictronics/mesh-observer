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
import argparse
import datetime

# trunk-ignore(bandit/B402)
import ftplib
import math
import os
import signal
import sqlite3
import sys
import threading
import time

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import schedule
import seaborn as sns
from d3graph import d3graph, vec2adjmat
from jinja2 import Environment, FileSystemLoader
from matplotlib.patches import Rectangle
from meshtastic.protobuf import config_pb2, mesh_pb2, portnums_pb2
from pubsub import pub

import globals as g
from http_repeater import HttpRepeaterServer
from repeater_core import RepeaterCore
from tcp_reader import TcpReader
from tcp_repeater import TcpRepeaterServer

__author__ = "Michael Wolf aka Mictronics"
__copyright__ = "2026, (C) Michael Wolf"
__license__ = "GPL v3+"
__version__ = "2.1.0"

DATABASE_FILE = "network.sqlite3"
CHART_COLOR = "limegreen"
LOCAL_TIMEZONE = "Europe/Berlin"

# Real Meshtastic port numbers (matches network.sqlite3.sql's packet_types
# table), for the packet types handled directly in _handle_tcp_packet().
PORT_NUM_TEXT = 1
PORT_NUM_POSITION = 3
PORT_NUM_NODEINFO = 4
PORT_NUM_ADMIN = 6
PORT_NUM_WAYPOINT = 8
PORT_NUM_TRACEROUTE = 70

# Synthetic telemetry sub-type ports this project invented (see
# network.sqlite3.sql's packet_types 512-519, 517 reserved/unused) to
# disambiguate what the real protocol collapses onto the single TELEMETRY_APP
# port (67); keyed by which
# oneof field is present on the decoded Telemetry message, the same
# discriminator the meshtastic library's own _onTelemetryReceive uses.
TELEMETRY_PORTS = {
    "deviceMetrics": (512, "DeviceTelemetry"),
    "powerMetrics": (513, "PowerTelemetry"),
    "environmentMetrics": (514, "EnvironmentTelemetry"),
    "hostMetrics": (515, "HostMetrics"),
    "airQualityMetrics": (516, "AirQuality"),
    # 517 (Health Telemetry) is intentionally unmapped: the firmware feature
    # it reports on is disabled by default and requires a manual rebuild to
    # enable, so it's never expected on the wire -- unclassified health
    # metrics fall through to the generic (67, "telemetry") bucket instead.
    "localStats": (518, "LocalStats"),
    "trafficManagementStats": (519, "TrafficManagementStats"),
}


def initArgParser():
    """Initialize the command line argument parsing."""
    parser = g.parser

    parser.add_argument(
        "-g",
        "--graph",
        help="Visualize the Meshtastic network from database",
        action="store_true",
    )

    parser.add_argument(
        "-s",
        "--stats",
        help="Generate the Meshtastic network statistics from database",
        action="store_true",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        help="Include LOG_DEBUG messages (repeater handshake/forwarding detail) in log output",
        action="store_true",
    )

    parser.add_argument("--version", action="version", version=f"{__version__}")

    g.args = parser.parse_args()


def ftp_upload(hourly=False):
    """Upload generated web content via FTP to remote server."""
    reader = g.reader
    try:
        # Imported lazily: ftp_credentials.py is a hand-written, gitignored file
        # that only needs to exist when FTP upload is actually used (see README).
        # Importing it here, inside the try, means a missing file is just logged
        # and skipped instead of crashing the caller.
        import ftp_credentials

        ftp_server = ftplib.FTP_TLS(
            ftp_credentials.__hostname__,
            ftp_credentials.__username__,
            ftp_credentials.__password__,
            timeout=5,
        )
        ftp_server.encoding = "utf-8"
        # Change to the target remote folder, creating it first if it doesn't exist yet
        try:
            ftp_server.cwd(ftp_credentials.__remote_folder__)
        except Exception:
            ftp_server.mkd(ftp_credentials.__remote_folder__)
            ftp_server.cwd(ftp_credentials.__remote_folder__)

        # Upload only hourly packet stats
        if hourly:
            filename = ftp_credentials.__local_folder__ + "/stats.png"
            with open(filename, "rb") as file:
                ftp_server.storbinary("STOR stats.png", file)
            filename = ftp_credentials.__local_folder__ + "/decoding.png"
            with open(filename, "rb") as file:
                ftp_server.storbinary("STOR decoding.png", file)
            ftp_server.quit()
            return

        # Upload entire web folder including sub-folders
        for root, _dirs, files in os.walk(ftp_credentials.__local_folder__):
            rel_path = os.path.relpath(root, ftp_credentials.__local_folder__)
            ftp_path = os.path.join(
                ftp_credentials.__remote_folder__, rel_path
            ).replace("\\", "/")

            # Ensure remote directory exists
            try:
                ftp_server.cwd(ftp_path)
            except Exception:
                # Create intermediate directories
                parts = ftp_path.strip("/").split("/")
                curr_path = ""
                for part in parts:
                    curr_path += "/" + part
                    try:
                        ftp_server.cwd(curr_path)
                    except Exception:
                        ftp_server.mkd(curr_path)
                        ftp_server.cwd(curr_path)

            # Upload files in the current directory
            for filename in files:
                local_file = os.path.join(root, filename)
                remote_file = filename
                with open(local_file, "rb") as f:
                    ftp_server.storbinary(f"STOR {remote_file}", f)

        ftp_server.quit()
    except Exception as e:
        reader.log(f"FTP upload failed. Error: {e}", level=reader.LOG_ERR)


def statistics(hourly=False):
    """Create statistics and web content."""
    lock = g.lock
    reader = g.reader
    database = None
    plt.set_loglevel("WARNING")
    node_count = 0
    link_count = 0
    module_count = g.module_count
    statistics = {}
    dt = datetime.datetime.now()
    now_str = dt.strftime("%d.%m.%Y %H:%M")  # Web content update time

    roles = [
        "Client",
        "Client Mute",
        "Router",
        "Router Client",
        "Repeater",
        "Tracker",
        "Sensor",
        "TAK",
        "Client Hidden",
        "Lost and Found",
        "TAK Tracker",
        "Router Late",
        "Client Base",
    ]

    try:
        # Create packets/24h statistics
        if module_count["startlog"] is not None:
            diff_sec = (
                datetime.datetime.now() - module_count["startlog"]
            ).total_seconds()
            STAT_LABELS = [
                ("Device Telemetry", "DeviceTelemetry"),
                ("Environment Telemetry", "EnvironmentTelemetry"),
                ("Host Metrics", "HostMetrics"),
                ("Power Telemetry", "PowerTelemetry"),
                ("Traceroute", "traceroute"),
                ("Position", "position"),
                ("NodeInfo", "nodeinfo"),
                ("Text", "text msg"),
                ("Waypoint", "waypoint msg"),
                ("Air Quality", "AirQuality"),
                ("Local Stats", "LocalStats"),
                ("Traffic Management Stats", "TrafficManagementStats"),
                ("Admin", "admin"),
            ]
            for label, key in STAT_LABELS:
                statistics[label] = math.ceil((module_count[key] / diff_sec) * 60 * 60)
            statistics = dict(
                sorted(statistics.items(), key=lambda item: item[1], reverse=True)
            )

            stats_plot = sns.barplot(
                data=statistics,
                color=CHART_COLOR,
                orient="h",
            )
            df = pd.DataFrame(statistics.items())
            total = df[1].sum()
            if total == 0:  # Prevent division by zero
                total = 1
            for index, row in df.iterrows():
                plt.text(
                    row[1],
                    index,
                    f"{row[1]} / {(row[1] / total) * 100:.1f}%",
                    color="black",
                    va="center",
                )
            stats_plot.set_xlabel("Packete / Stunde")
            stats_plot.set_ylabel("Packet Typ")
            stats_plot.set(title="Messzeit: " + now_str)
            stats_plot.figure.suptitle("Packete / Stunde")
            plt.savefig(os.getcwd() + "/web/stats.png", dpi=100, bbox_inches="tight")
            plt.close()

            # Create decoding statistics graph
            decoding = {}
            decoding["Entschlüsselt"] = module_count.get("decoded", 0)
            decoding["Verschlüsselt"] = module_count.get("encrypted", 0)
            decoding_plot = sns.barplot(
                data=decoding,
                color=CHART_COLOR,
                orient="h",
                width=0.4,
            )
            df = pd.DataFrame(decoding.items())
            total = df[1].sum()
            if total == 0:  # Prevent division by zero
                total = 1
            for index, row in df.iterrows():
                plt.text(
                    row[1],
                    index,
                    f"{row[1]} / {(row[1] / total) * 100:.1f}%",
                    color="black",
                    va="center",
                )
            decoding_plot.set_xlabel("Packete")
            decoding_plot.set_ylabel("Status")
            decoding_plot.set(title="Messzeit: " + now_str)
            decoding_plot.figure.suptitle("Anteil privater Packete im Messzeitraum")
            decoding_plot.figure.set_size_inches(8, 4)
            plt.savefig(os.getcwd() + "/web/decoding.png", dpi=100, bbox_inches="tight")
            plt.close()

            # Reset all counters and restart the measurement window for the next hour.
            # module_count is g.module_count itself, so mutating it in place is enough.
            for key in module_count:
                if key != "startlog":
                    module_count[key] = 0
            module_count["startlog"] = datetime.datetime.now()

        if hourly:
            # Do nothing else when called hourly
            return

        with lock:
            # Fetch packet data from database
            database = sqlite3.connect(DATABASE_FILE, isolation_level="DEFERRED")
            cur = database.cursor()
            res = cur.execute(
                "SELECT count(*) FROM nodes where seen > unixepoch(datetime('now', '-24 hours'));"
            )
            node_count = res.fetchone()[0]
            res = cur.execute(
                "SELECT count(*) FROM links where seen > unixepoch(datetime('now', '-24 hours'));"
            )
            link_count = res.fetchone()[0]

            query = "SELECT * FROM ViewPackets;"
            packets = pd.read_sql(query, database)
            # Correct UTC timestamps to local timezone
            packets["time"] = (
                pd.to_datetime(packets["time"], unit="s")
                .dt.tz_localize("UTC")
                .dt.tz_convert(LOCAL_TIMEZONE)
            )

        # Set global plot parameters
        plt.set_loglevel("WARNING")
        sns.set_style("whitegrid")
        sns.set_context("paper")
        formatter = mdates.DateFormatter("%d.%m.%Y", tz="CEST")

        total_packets = packets.shape[0]
        html_nodes = []
        # Get the overall packet data period
        min_t = packets["time"].min().strftime("%d.%m.%Y")
        max_t = packets["time"].max().strftime("%d.%m.%Y")
        period = f"{min_t} - {max_t}"
        # Get top 10 data
        top10_packets = (
            packets.groupby(["source", "longname"])["type"]
            .count()
            .nlargest(10)
            .to_dict()
        )
        top10_types = (
            packets.groupby(["longname", "port_name"])["type"]
            .count()
            .nlargest(10, "first")
            .to_dict()
        )
        # Create hourly heatmap graph
        grouped = packets.groupby([packets["time"].dt.day, packets["time"].dt.hour])
        hourly_counts = grouped.size().unstack(fill_value=0)
        # Get the maximum value and its index
        max_idx = hourly_counts.stack().idxmax()
        max_y = hourly_counts.index.get_loc(max_idx[0])
        max_x = hourly_counts.columns.get_loc(max_idx[1])
        plt.figure(figsize=(12, 4))
        cmap = sns.light_palette(CHART_COLOR, n_colors=5)
        hourly_plot = sns.heatmap(hourly_counts, cmap=cmap, annot=True, fmt="d")
        # Highlight the maximum value in the heatmap
        hourly_plot.add_patch(
            Rectangle((max_x, max_y), 1, 1, fill=False, edgecolor="red", lw=1)
        )
        hourly_plot.figure.suptitle("Pakete pro Tag über Stunden")
        hourly_plot.set_xlabel("Stunde")
        hourly_plot.set_ylabel("Tag")
        plt.savefig(
            os.getcwd() + "/web/hourly_heatmap.png", dpi=100, bbox_inches="tight"
        )
        plt.close()
        # Create number of nodes distribution over hours graph
        daily_nodes_nunique = (
            packets.groupby([packets["time"].dt.hour]).source.nunique().to_numpy()
        )
        daily_plot = sns.barplot(data=daily_nodes_nunique, color=CHART_COLOR)
        daily_plot.set_xlabel("Stunde")
        daily_plot.set_ylabel("Knoten")
        daily_plot.set(title="Messzeitraum: " + period)
        daily_plot.figure.suptitle(
            "Verteilung eindeutige Knoten über die Tageszeit im Messzeitraum"
        )
        daily_plot.axhline(y=40).set_color("red")
        plt.savefig(os.getcwd() + "/web/daily.png", dpi=100, bbox_inches="tight")
        plt.close()
        # Create number of packets distribution over days per week graph
        weekly_packets = packets.groupby([packets["time"].dt.day]).type.count()
        weekly_plot = sns.barplot(
            data=weekly_packets,
            color=CHART_COLOR,
            estimator="sum",
            errorbar=None,
            orient="v",
        )
        weekly_plot.set_xlabel("Tag")
        weekly_plot.set_ylabel("Packete")
        weekly_plot.set(title="Messzeitraum: " + period)
        weekly_plot.figure.suptitle("Anzahl der Packete pro Tag im Messzeitraum")
        for cont in weekly_plot.containers:
            weekly_plot.bar_label(cont, fontsize=8)
        plt.savefig(os.getcwd() + "/web/weekly.png", dpi=100, bbox_inches="tight")
        plt.close()

        # Create hop-count distribution graph (mesh diameter indicator)
        hops = packets["hops_used"].dropna()
        if not hops.empty:
            hop_counts = hops.value_counts().sort_index()
            plt.figure(figsize=(6, 4))
            hops_plot = sns.barplot(
                x=hop_counts.index.astype(int), y=hop_counts.values, color=CHART_COLOR
            )
            hops_plot.set_xlabel("Anzahl Hops")
            hops_plot.set_ylabel("Packete")
            hops_plot.set(title="Messzeitraum: " + period)
            hops_plot.figure.suptitle("Hop-Verteilung im Mesh")
            plt.savefig(os.getcwd() + "/web/hops.png", dpi=100, bbox_inches="tight")
            plt.close()

        # Create channel/airtime utilization per node graph (congestion indicator)
        airtime = packets.dropna(subset=["air_util_tx"]).groupby("longname")["air_util_tx"].mean().sort_values(
            ascending=False
        )
        if not airtime.empty:
            plt.figure(figsize=(8, max(4, 0.3 * len(airtime))))
            airtime_plot = sns.barplot(x=airtime.values, y=airtime.index, color=CHART_COLOR, orient="h")
            airtime_plot.set_xlabel("Kanalauslastung TX (%)")
            airtime_plot.set_ylabel("Knoten")
            airtime_plot.set(title="Messzeitraum: " + period)
            airtime_plot.figure.suptitle("Kanalauslastung (Airtime) pro Knoten")
            plt.savefig(os.getcwd() + "/web/airtime.png", dpi=100, bbox_inches="tight")
            plt.close()

        # Create average RX SNR per node graph (weak-link indicator, worst first)
        snr = packets.dropna(subset=["rx_snr"]).groupby("longname")["rx_snr"].mean().sort_values()
        if not snr.empty:
            plt.figure(figsize=(8, max(4, 0.3 * len(snr))))
            snr_plot = sns.barplot(x=snr.values, y=snr.index, color=CHART_COLOR, orient="h")
            snr_plot.set_xlabel("Ø SNR (dB)")
            snr_plot.set_ylabel("Knoten")
            snr_plot.set(title="Messzeitraum: " + period)
            snr_plot.figure.suptitle("Empfangsqualität pro Knoten (SNR)")
            plt.savefig(os.getcwd() + "/web/snr.png", dpi=100, bbox_inches="tight")
            plt.close()

        # Create packet statistics graph for each node
        for node, node_packets in packets.groupby(["source", "longname"]):
            node_id = node[0]
            long_name = node[1]
            role_int = node_packets["role"].unique()[0]
            if role_int < len(roles):
                role = roles[role_int]
            else:
                role = "Unbekannte Rolle"
            packet_count = node_packets.shape[0]
            load = 100 * (packet_count / total_packets)
            # Skip nodes with mesh load less than 0.25%
            if load < 0.25:
                continue
            # Add node to dataframe
            html_nodes.append(
                dict(
                    id=f"{node_id:08X}",
                    long_name=long_name,
                    packet_count=packet_count,
                    load=round(load, 3),
                    role=role,
                )
            )
            # Create single node statistics graph
            node_plot = sns.catplot(
                data=node_packets,
                x="time",
                y="port_name",
                jitter=False,
                height=2,
                aspect=4,
            )
            node_plot.ax.xaxis.set_major_formatter(formatter)
            node_plot.set_axis_labels("Zeitraum: " + period, "Packet Typ")
            node_plot.set(autoscalex_on=True)
            node_plot.set_xticklabels(rotation=45, ha="right", step=2)
            node_plot.set(
                title=f"{long_name} / {node_id:08X} / Mesh Last: {load:0.2f}%"
            )
            # Calculate mean interval for each packet type of a single node
            delta_t_stats = {}
            for packet_group, packet_details in node_packets.groupby(["port_name"]):
                time_cnt = packet_details["time"].count()
                if time_cnt > 1:
                    packet_details["delta_t"] = (
                        packet_details["time"].diff().dt.total_seconds()
                    )
                    stat = packet_details["delta_t"].agg(
                        ["median", "count"]
                    )  # "median", "mean", "min", "max"
                    mean_str = str(
                        datetime.timedelta(seconds=math.ceil(stat["median"]))
                    )
                    delta_t_stats[packet_group[0]] = "Median: " + mean_str
            # Add mean value to each packet type in graph
            for ax in node_plot.axes.flat:
                labels = ax.get_yticklabels()
                for label in labels:
                    _, y = label.get_position()
                    txt = label.get_text()
                    h = 0.76 / (len(labels) + 1)
                    node_plot.figure.text(
                        1.0, 0.97 - h - (h * y), delta_t_stats.get(txt, "N/A")
                    )
            # Save node statistics graph
            plt.savefig(
                f"{os.getcwd()}/web/images/{node_id:08X}.png",
                dpi=100,
                bbox_inches="tight",
            )
            plt.close()

        # Generate statistical web content
        html_nodes.sort(key=lambda x: x["load"], reverse=True)
        jinja_env = Environment(
            loader=FileSystemLoader("index.html.j2"), autoescape=True
        )
        index_template = jinja_env.get_template("")
        html = index_template.render(
            html_nodes=html_nodes,
            period=period,
            total_packets=total_packets,
            statistics=statistics,
            top10_packets=top10_packets,
            top10_types=top10_types,
            last_update=now_str,
            link_count=link_count,
            node_count=node_count,
        )
        # Save generated web content
        index_file = os.getcwd() + "/web/index.html"
        with open(index_file, "w", encoding="utf-8") as f:
            f.write(html)

    except Exception as e:
        reader.log(
            f"Creating network statistics failed. Error: {e}", level=reader.LOG_ERR
        )

    finally:
        if database is not None:
            database.close()


def graph(full=False):
    lock = g.lock
    reader = g.reader

    sources = []
    destinations = []
    edge_labels = []
    nodes = {}
    database = None

    try:
        with lock:
            database = sqlite3.connect(DATABASE_FILE, isolation_level="DEFERRED")
            cur = database.cursor()
            if full:
                res = cur.execute("select * from nodes;")
            else:
                res = cur.execute(
                    "select * from nodes where seen > unixepoch(datetime('now', '-24 hours'));"
                )
            for row in res:
                nodes[f"{row[0]:08X}"] = {
                    "short": row[1],
                    "long": row[2],
                    "seen": row[3],
                }

            if full:
                res = cur.execute("select * from links;")
            else:
                res = cur.execute(
                    "select * from links where seen > unixepoch(datetime('now', '-24 hours'));"
                )
            for row in res:
                src = row[0]
                dst = row[1]
                snr = row[2]
                sources.append(f"{src:08X}")
                destinations.append(f"{dst:08X}")
                if snr <= -500:
                    edge_labels.append("? dB")
                else:
                    edge_labels.append(f"{snr:0.2f} dB")
            cur.close()

        d3 = d3graph(
            charge=2000, slider=None, verbose=40, support="Mictronics", collision=3
        )
        adjmat = vec2adjmat(sources, destinations, weight=None)
        d3.graph(adjmat, cmap="tab20")
        d3.set_path(os.getcwd() + "/web/visualization.html")
        for n in range(len(sources)):
            d3.edge_properties[sources[n], destinations[n]]["label"] = edge_labels[n]
            d3.edge_properties[sources[n], destinations[n]]["directed"] = True

        for node in d3.node_properties:
            if node in nodes.keys():
                dt = datetime.datetime.fromtimestamp(nodes[node]["seen"])
                last = dt.strftime("%d.%m.%Y %H:%M:%S")
                d3.node_properties[node]["cmap"] = "tab20"
                if nodes[node]["short"] is not None:
                    short = nodes[node]["short"]
                    long = nodes[node]["long"]
                    d3.node_properties[node]["tooltip"] = f"{short}\n{long}\n{last}"
                    d3.node_properties[node]["label"] = short

        d3.show(
            filepath=os.getcwd() + "/web/visualization.html",
            show_slider=False,
            title="Meshtastic Netzwerk Bayern",
            figsize=[None, None],
            showfig=False,
            save_button=False,
        )

    except Exception as e:
        reader.log(f"Creating network graph failed. Error: {e}", level=reader.LOG_ERR)

    finally:
        if database is not None:
            database.close()


def _insert_packet(
    database, lock, source_id, port_num, hops_used=None, rx_snr=None, rx_rssi=None, channel_util=None, air_util_tx=None
):
    with lock:
        cur = database.cursor()
        cur.executemany(
            "INSERT OR REPLACE INTO packets VALUES(:id, :type, strftime('%s','now'), :hops_used, :rx_snr, :rx_rssi, :channel_util, :air_util_tx);",
            [
                {
                    "id": source_id,
                    "type": port_num,
                    "hops_used": hops_used,
                    "rx_snr": rx_snr,
                    "rx_rssi": rx_rssi,
                    "channel_util": channel_util,
                    "air_util_tx": air_util_tx,
                }
            ],
        )
        database.commit()
        cur.close()


def _upsert_node(database, lock, node_id, shortname=None, longname=None, role=None, hw=None):
    # role/hw default to None (not 0) so a bare "seen" touch (e.g. from a
    # traceroute endpoint) never clobbers an already-known node's role/hardware.
    with lock:
        cur = database.cursor()
        cur.executemany(
            "INSERT INTO nodes VALUES(:id, :shortname, :longname, strftime('%s','now'), NULL, NULL, coalesce(:role, 0), coalesce(:hw, 0)) "
            "ON CONFLICT(id) DO UPDATE SET shortname=coalesce(:shortname, shortname), "
            "longname=coalesce(:longname, longname), seen=strftime('%s','now'), "
            "role=coalesce(:role, role), hardware=coalesce(:hw, hardware);",
            [{"id": node_id, "shortname": shortname, "longname": longname, "role": role, "hw": hw}],
        )
        database.commit()
        cur.close()


def _enum_int(enum_type, name, default=0):
    """Resolve a protobuf enum name (as given by MessageToDict) back to its int
    value -- the nodes table stores role/hardware as INTEGER (see statistics()'s
    roles[role_int] lookup), but the decoded packet dict carries enum names.
    """
    if not name:
        return default
    try:
        return enum_type.Value(name)
    except ValueError:
        return default


def _classify_telemetry(telemetry):
    """Discriminate a decoded TELEMETRY_APP packet's sub-type the same way the
    meshtastic library's own _onTelemetryReceive does: by which oneof key is
    present in the decoded dict.
    """
    for key, result in TELEMETRY_PORTS.items():
        if key in telemetry:
            return result
    return 67, "telemetry"  # Fallback: generic Telemetry (e.g. distance-sensor payloads)


def _handle_tcp_packet(database, lock, module_count, reader, packet):
    """Write one decoded packet (from the "meshtastic.receive" pypubsub topic)
    into the DB. Own-node packets update node metadata but are excluded from
    both the packets table and the per-type module_count counters, so every
    statistics() chart (hourly and long-term) reflects only real mesh
    traffic, not the connected-API chatter (e.g. our own node pushing its
    position/telemetry every minute) that was never actually sent over LoRa.
    """
    decoded = packet.get("decoded")
    if decoded is None:
        module_count["encrypted"] = module_count.get("encrypted", 0) + 1
        return
    module_count["decoded"] = module_count.get("decoded", 0) + 1

    from_id = packet.get("from")
    if from_id is None or from_id in (0, 0xFFFFFFFF):
        return

    iface = reader.iface
    is_own = (
        iface is not None
        and iface.myInfo is not None
        and from_id == iface.myInfo.my_node_num
    )
    portnum = decoded.get("portnum", "")

    # hopStart/hopLimit/rxSnr/rxRssi live on the MeshPacket envelope itself
    # (not inside `decoded`), and are set by our own node's radio for
    # whichever hop last relayed the packet to us -- available regardless of
    # port type, so extracted once here rather than per-branch.
    hop_start = packet.get("hopStart")
    hop_limit = packet.get("hopLimit")
    hops_used = hop_start - hop_limit if hop_start is not None and hop_limit is not None else None
    rx_snr = packet.get("rxSnr")
    rx_rssi = packet.get("rxRssi")

    if portnum == "NODEINFO_APP":
        user = decoded.get("user", {})
        _upsert_node(
            database,
            lock,
            from_id,
            shortname=user.get("shortName"),
            longname=user.get("longName"),
            role=_enum_int(config_pb2.Config.DeviceConfig.Role, user.get("role")),
            hw=_enum_int(mesh_pb2.HardwareModel, user.get("hwModel")),
        )
        if not is_own:
            module_count["nodeinfo"] += 1
            _insert_packet(database, lock, from_id, PORT_NUM_NODEINFO, hops_used=hops_used, rx_snr=rx_snr, rx_rssi=rx_rssi)

    elif portnum == "POSITION_APP":
        position = decoded.get("position", {})
        lat = position.get("latitudeI", 0) * 1e-7
        lon = position.get("longitudeI", 0) * 1e-7
        # lat=0/lon=0 means "no GPS fix"; skip to avoid overwriting a node's
        # last known position with Null Island.
        if lat != 0 or lon != 0:
            with lock:
                cur = database.cursor()
                cur.executemany(
                    "UPDATE OR IGNORE nodes SET seen = strftime('%s','now'), latitude = :lat, longitude = :lon WHERE id = :id;",
                    [{"id": from_id, "lat": lat, "lon": lon}],
                )
                database.commit()
                cur.close()
        if not is_own:
            module_count["position"] += 1
            _insert_packet(database, lock, from_id, PORT_NUM_POSITION, hops_used=hops_used, rx_snr=rx_snr, rx_rssi=rx_rssi)

    elif portnum == "TRACEROUTE_APP":
        traceroute = decoded.get("traceroute", {})
        # Unlike the old MQTT JSON format (long names only, or "Unknown"), the
        # TCP API gives real node numbers for the whole route -- write every
        # hop-to-hop link, not just the endpoints.
        route = [from_id, *traceroute.get("route", []), packet.get("to")]
        snr_towards = traceroute.get("snrTowards") or []
        with lock:
            cur = database.cursor()
            for i in range(len(route) - 1):
                src, dst = route[i], route[i + 1]
                if src in (0, 0xFFFFFFFF, None) or dst in (0, 0xFFFFFFFF, None) or src == dst:
                    continue
                snr = snr_towards[i] / 4 if i < len(snr_towards) else -500
                cur.executemany(
                    "INSERT OR REPLACE INTO links VALUES(:source, :destination, :snr, strftime('%s','now'));",
                    [{"source": src, "destination": dst, "snr": snr}],
                )
                cur.executemany(
                    "INSERT INTO nodes VALUES(:id, NULL, NULL, strftime('%s','now'), NULL, NULL, 0, 0) ON CONFLICT(id) DO UPDATE SET seen=strftime('%s','now');",
                    ({"id": src},),
                )
            database.commit()
            cur.close()
        _upsert_node(database, lock, from_id)
        if not is_own:
            module_count["traceroute"] += 1
            _insert_packet(database, lock, from_id, PORT_NUM_TRACEROUTE, hops_used=hops_used, rx_snr=rx_snr, rx_rssi=rx_rssi)

    elif portnum == "TELEMETRY_APP":
        telemetry = decoded.get("telemetry", {})
        port_num, counter_key = _classify_telemetry(telemetry)
        if not is_own:
            module_count[counter_key] = module_count.get(counter_key, 0) + 1
            device_metrics = telemetry.get("deviceMetrics", {})
            _insert_packet(
                database,
                lock,
                from_id,
                port_num,
                hops_used=hops_used,
                rx_snr=rx_snr,
                rx_rssi=rx_rssi,
                channel_util=device_metrics.get("channelUtilization"),
                air_util_tx=device_metrics.get("airUtilTx"),
            )

    elif portnum == "TEXT_MESSAGE_APP":
        if not is_own:
            module_count["text msg"] += 1
            _insert_packet(database, lock, from_id, PORT_NUM_TEXT, hops_used=hops_used, rx_snr=rx_snr, rx_rssi=rx_rssi)

    elif portnum == "WAYPOINT_APP":
        if not is_own:
            module_count["waypoint msg"] += 1
            _insert_packet(database, lock, from_id, PORT_NUM_WAYPOINT, hops_used=hops_used, rx_snr=rx_snr, rx_rssi=rx_rssi)

    elif portnum == "ADMIN_APP":
        if not is_own:
            module_count["admin"] += 1
            _insert_packet(database, lock, from_id, PORT_NUM_ADMIN, hops_used=hops_used, rx_snr=rx_snr, rx_rssi=rx_rssi)

    else:
        # Any other real portnum (see network.sqlite3.sql's packet_types for
        # display names) that doesn't need special field extraction -- still
        # counted and logged instead of silently dropped.
        port_num = _enum_int(portnums_pb2.PortNum, portnum, portnums_pb2.PortNum.UNKNOWN_APP)
        if port_num != portnums_pb2.PortNum.UNKNOWN_APP and not is_own:
            module_count[portnum] = module_count.get(portnum, 0) + 1
            _insert_packet(database, lock, from_id, port_num, hops_used=hops_used, rx_snr=rx_snr, rx_rssi=rx_rssi)


def tcpListener():
    """Thread body for the sole ingestion mode: hold the single upstream TCP
    API connection, write decoded packets to the DB, and repeat the same
    connection out to other Meshtastic API clients (repeater_core.py).
    """
    lock = g.lock
    reader = g.reader  # TcpReader
    ev_run = g.ev_run
    if ev_run is None:
        return

    try:
        # check_same_thread=False: the handler below runs on the meshtastic
        # library's own "publishing" thread, not this one. Writes are still
        # serialized through g.lock like every other DB access in this codebase.
        database = sqlite3.connect(
            DATABASE_FILE, isolation_level="DEFERRED", check_same_thread=False
        )
    except Exception as e:
        reader.log(f"Connection to database failed. Error: {e}", level=reader.LOG_ERR)
        ev_run.clear()
        return

    module_count = g.module_count
    module_count["startlog"] = datetime.datetime.now()

    import tcp_credentials

    core = RepeaterCore(reader)
    tcp_server = TcpRepeaterServer(
        "0.0.0.0", getattr(tcp_credentials, "__repeater_tcp_port__", 4403), core
    )
    http_server = HttpRepeaterServer(
        "0.0.0.0", getattr(tcp_credentials, "__repeater_http_port__", 4404), core
    )
    threading.Thread(target=tcp_server.serve_forever, name="TCP repeater", daemon=True).start()
    threading.Thread(target=http_server.serve_forever, name="HTTP repeater", daemon=True).start()

    def handle_packet(packet, interface):
        try:
            _handle_tcp_packet(database, lock, module_count, reader, packet)
        except Exception as e:
            reader.log(f"Failed handling packet: {e}", level=reader.LOG_WARNING)

    pub.subscribe(handle_packet, "meshtastic.receive")
    reader.log(
        f"TCP listener + repeater started as {reader.__class__.__name__}", level=reader.LOG_INFO
    )

    while ev_run.is_set():
        time.sleep(1)  # Actual work happens in handle_packet above.

    pub.unsubscribe(handle_packet, "meshtastic.receive")
    tcp_server.shutdown()
    http_server.shutdown()
    database.close()


def hourlyRunner():
    """Job running every hour"""
    statistics(hourly=True)
    ftp_upload(hourly=True)


def dailyRunner():
    """Job running twice per day"""
    graph()
    statistics(hourly=False)
    ftp_upload(hourly=False)


def scheduleRunner():
    """Thread running the scheduler"""
    reader = g.reader
    ev_run = g.ev_run
    if ev_run is None:
        return  # No event to run, exit the thread
    schedule.every().hour.at(":10").do(hourlyRunner)
    schedule.every().day.at("11:59:00", LOCAL_TIMEZONE).do(dailyRunner)
    schedule.every().day.at("23:59:00", LOCAL_TIMEZONE).do(dailyRunner)
    reader.log("Scheduler started", level=reader.LOG_INFO)
    while ev_run.is_set():
        schedule.run_pending()
        time.sleep(1)  # Avoid busy loop


def main():
    """Main program function"""
    g.lock = threading.Lock()
    threads = []
    ev_run = threading.Event()
    ev_run.set()

    def signal_handler(signal, frame):
        ev_run.clear()  # Stop all threads

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGABRT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(
        prog="meshtastic observer",
        description="Log and visualize statistics of a Meshtastic network.",
        epilog="License GPL-3+ (C) 2026 Michael Wolf, www.mictronics.de",
    )
    g.parser = parser
    initArgParser()
    args = g.args

    if args.graph:
        graph(full=True)
        sys.exit(0)

    if args.stats:
        statistics()
        sys.exit(0)

    g.ev_run = ev_run  # Store event in globals for other threads; TcpReader
    # needs it too, so its connect-retry loop can be interrupted by SIGINT/SIGTERM

    # Connect to the Meshtastic node's TCP API (see tcp_credentials.py)
    import tcp_credentials

    reader = TcpReader(
        tcp_credentials.__hostname__, tcp_credentials.__port__, ev_run, verbose=args.verbose
    )

    g.reader = reader  # Store reader in globals for other threads

    # The threads we are running
    t = threading.Thread(target=tcpListener, name="Log Parser")
    t.daemon = True  # Daemon thread will exit when the main program exits
    threads.append(t)
    t = threading.Thread(target=scheduleRunner, name="Scheduler")
    t.daemon = True  # Daemon thread will exit when the main program exits
    threads.append(t)

    # Start each thread
    for t in threads:
        t.start()

    # Wait for all threads to finish
    for t in threads:
        t.join()

    reader.close()  # Close the reader connection
    sys.exit(0)  # Exit the program


if __name__ == "__main__":
    main()
