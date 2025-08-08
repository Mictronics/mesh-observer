#!python3

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
import argparse
import os
import signal
import sys
import re
import datetime
import time
import threading
# trunk-ignore(bandit/B402)
import ftplib
import math
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import seaborn as sns
import sqlite3
import ftp_credentials
import schedule

from jinja2 import Environment, FileSystemLoader
from globals import Globals
from d3graph import d3graph, vec2adjmat
from journal_reader import JournalReader
from serial_reader import SerialReader


__author__ = "Michael Wolf aka Mictronics"
__copyright__ = "2025, (C) Michael Wolf"
__license__ = "GPL v3+"
__version__ = "1.0.0"


def initArgParser():
    """Initialize the command line argument parsing."""
    _globals = Globals.getInstance()
    parser = _globals.getParser()
    args = _globals.getArgs()

    parser.add_argument(
        "--dev",
        help="Where the serial Meshtastic device is connected to, i.e. /dev/ttyUSB0",
        default=None,
        required=False,
    )

    parser.add_argument(
        "-g",
        "--graph",
        help="Visualize the Meshtastic network from database",
        action='count',
        default=0,
        required=False,
    )

    parser.add_argument(
        "-s",
        "--stats",
        help="Generate the Meshtastic network statistics from database",
        action='count',
        default=0,
        required=False,
    )

    parser.set_defaults(deprecated=None)
    parser.add_argument("--version", action="version",
                        version=f"{__version__}")

    args = parser.parse_args()
    _globals.setArgs(args)
    _globals.setParser(parser)


def ftp_upload(hourly=False):
    """Upload generated web content via FTP to remote server."""
    # Change to the target remote folder (create if necessary)
    try:
        ftp_server = ftplib.FTP_TLS(
            ftp_credentials.__hostname__, ftp_credentials.__username__, ftp_credentials.__password__, timeout=5)
        ftp_server.encoding = "utf-8"
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
            ftp_credentials.__remote_folder__, rel_path).replace("\\", "/")

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
            with open(local_file, 'rb') as f:
                ftp_server.storbinary(f'STOR {remote_file}', f)

    ftp_server.quit()


def statistics(hourly=False):
    """Create statistics and web content."""
    _globals = Globals.getInstance()
    lock = _globals.getLock()
    reader = _globals.getReader()
    database = None
    plt.set_loglevel('WARNING')
    node_count = 0
    link_count = 0
    module_count = _globals.getModuleCount()
    statistics = {}
    dt = datetime.datetime.now()
    now_str = dt.strftime('%d.%m.%Y %H:%M')  # Web content update time

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
    ]

    try:
        # Create packets/24h statistics
        if module_count['startlog'] is not None:
            diff_sec = (datetime.datetime.now() -
                        module_count['startlog']).total_seconds()
            statistics['Device Telemetry'] = math.ceil(
                (module_count['DeviceTelemetry'] / diff_sec) * 60 * 60)
            statistics['Environment Telemetry'] = math.ceil(
                (module_count['EnvironmentTelemetry'] / diff_sec) * 60 * 60)
            statistics['Host Metrics'] = math.ceil(
                (module_count['HostMetrics'] / diff_sec) * 60 * 60)
            statistics['Store Forward'] = math.ceil(
                (module_count['StoreForward'] / diff_sec) * 60 * 60)
            statistics['Power Telemetry'] = math.ceil(
                (module_count['PowerTelemetry'] / diff_sec) * 60 * 60)
            statistics['Traceroute'] = math.ceil(
                (module_count['traceroute'] / diff_sec) * 60 * 60)
            # statistics['Routing'] = math.ceil((module_count['routing'] / diff_sec) * 60 * 60)
            statistics['Position'] = math.ceil(
                (module_count['position'] / diff_sec) * 60 * 60)
            statistics['NodeInfo'] = math.ceil(
                (module_count['nodeinfo'] / diff_sec) * 60 * 60)
            statistics['Text'] = math.ceil(
                (module_count['text msg'] / diff_sec) * 60 * 60)
            statistics['Waypoint'] = math.ceil(
                (module_count['waypoint msg'] / diff_sec) * 60 * 60)
            statistics['External Notification'] = math.ceil(
                (module_count['ExternalNotificationModule'] / diff_sec) * 60 * 60)
            statistics['Air Quality'] = math.ceil(
                (module_count['AirQuality'] / diff_sec) * 60 * 60)
            statistics['Admin'] = math.ceil(
                (module_count['admin'] / diff_sec) * 60 * 60)
            statistics['Error7'] = math.ceil(
                (module_count['error7'] / diff_sec) * 60 * 60)
            statistics = dict(
                sorted(statistics.items(), key=lambda item: item[1], reverse=True))

            stats_plot = sns.barplot(
                data=statistics,
                color="limegreen",
                orient='h',
            )
            df = pd.DataFrame(statistics.items())
            sum = df[1].sum()
            if sum == 0:  # Prevent division by zero
                sum = 1
            for index, row in df.iterrows():
                plt.text(
                    row[1], index, f"{row[1]} / {(row[1] / sum) * 100:.1f}%", color='black', va="center")
            stats_plot.set_xlabel("Packete / Stunde")
            stats_plot.set_ylabel("Packet Typ")
            stats_plot.set(title="Messzeit: " + now_str)
            stats_plot.figure.suptitle("Packete / Stunde")
            plt.savefig(os.getcwd() + "/web/stats.png",
                        dpi=100, bbox_inches="tight")
            plt.close()

            # Create decoding statistics graph
            decoding = {}
            decoding["Entschlüsselt"] = module_count.get("decoded", 0)
            decoding["Verschlüsselt"] = module_count.get("encrypted", 0)
            decoding_plot = sns.barplot(
                data=decoding,
                color="limegreen",
                orient='h',
                width=0.4,
            )
            df = pd.DataFrame(decoding.items())
            sum = df[1].sum()
            if sum == 0:  # Prevent division by zero
                sum = 1
            for index, row in df.iterrows():
                plt.text(
                    row[1], index, f"{row[1]} / {(row[1] / sum) * 100:.1f}%", color='black', va="center")
            decoding_plot.set_xlabel("Packete")
            decoding_plot.set_ylabel("Status")
            decoding_plot.set(title="Messzeit: " + now_str)
            decoding_plot.figure.suptitle(
                "Anteil privater Packete im Messzeitraum")
            decoding_plot.figure.set_size_inches(8, 4)
            plt.savefig(os.getcwd() + "/web/decoding.png",
                        dpi=100, bbox_inches="tight")
            plt.close()

        if hourly:
            # Do nothing else when called hourly
            return

        with lock:
            # Fetch packet data from database
            database = sqlite3.connect(
                "network.sqlite3", isolation_level='DEFERRED')
            cur = database.cursor()
            res = cur.execute(
                "SELECT count(*) FROM nodes where seen > unixepoch(datetime('now', '-24 hours'));")
            node_count = res.fetchone()[0]
            res = cur.execute(
                "SELECT count(*) FROM links where seen > unixepoch(datetime('now', '-24 hours'));")
            link_count = res.fetchone()[0]

            query = "SELECT * FROM ViewPackets;"
            packets = pd.read_sql(query, database)
            # Correct UTC timestamps to local timezone
            packets["time"] = pd.to_datetime(packets["time"], unit="s").dt.tz_localize(
                "UTC").dt.tz_convert("Europe/Berlin")

        # Set global plot parameters
        plt.set_loglevel('WARNING')
        sns.set_style("whitegrid")
        sns.set_context("paper")
        formatter = mdates.DateFormatter("%d.%m.%Y", tz="CEST")

        total_packets = packets.shape[0]
        html_nodes = []
        # Get the overall packet data period
        min_t = packets["time"].min().strftime('%d.%m.%Y')
        max_t = packets["time"].max().strftime('%d.%m.%Y')
        period = f"{min_t} - {max_t}"
        # Get top 10 data
        top10_packets = packets.groupby(["source", "longname"])[
            "type"].count().nlargest(10).to_dict()
        top10_types = packets.groupby(["longname", "port_name"])[
            "type"].count().nlargest(10, "first").to_dict()
        # Create hourly heatmap graph
        grouped = packets.groupby(
            [packets['time'].dt.day, packets['time'].dt.hour])
        hourly_counts = grouped.size().unstack(fill_value=0)
        # Get the maximum value and its index
        max_idx = hourly_counts.stack().idxmax()
        max_y = hourly_counts.index.get_loc(max_idx[0])
        max_x = hourly_counts.columns.get_loc(max_idx[1])
        plt.figure(figsize=(12, 4))
        cmap = sns.light_palette("limegreen", n_colors=5)
        hourly_plot = sns.heatmap(
            hourly_counts, cmap=cmap, annot=True, fmt="d")
        # Highlight the maximum value in the heatmap
        hourly_plot.add_patch(Rectangle((max_x, max_y), 1, 1,
                                        fill=False, edgecolor='red', lw=1))
        hourly_plot.figure.suptitle("Pakete pro Tag über Stunden")
        hourly_plot.set_xlabel("Stunde")
        hourly_plot.set_ylabel("Tag")
        plt.savefig(os.getcwd() + "/web/hourly_heatmap.png",
                    dpi=100, bbox_inches="tight")
        plt.close()
        # Create number of nodes distribution over hours graph
        daily_nodes_nunique = packets.groupby(
            [packets['time'].dt.hour]).source.nunique().to_numpy()
        daily_plot = sns.barplot(
            data=daily_nodes_nunique,
            color="limegreen"
        )
        daily_plot.set_xlabel("Stunde")
        daily_plot.set_ylabel("Knoten")
        daily_plot.set(title="Messzeitraum: " + period)
        daily_plot.figure.suptitle(
            "Verteilung eindeutige Knoten über die Tageszeit im Messzeitraum")
        daily_plot.axhline(y=40).set_color("red")
        plt.savefig(os.getcwd() + "/web/daily.png",
                    dpi=100, bbox_inches="tight")
        plt.close()
        # Create number of packets distribution over days per week graph
        weekly_packets = packets.groupby([packets['time'].dt.day]).type.count()
        weekly_plot = sns.barplot(
            data=weekly_packets,
            color="limegreen",
            estimator="sum",
            errorbar=None,
            orient='v',
        )
        weekly_plot.set_xlabel("Tag")
        weekly_plot.set_ylabel("Packete")
        weekly_plot.set(title="Messzeitraum: " + period)
        weekly_plot.figure.suptitle(
            "Anzahl der Packete pro Tag im Messzeitraum")
        for cont in weekly_plot.containers:
            weekly_plot.bar_label(cont, fontsize=8)
        plt.savefig(os.getcwd() + "/web/weekly.png",
                    dpi=100, bbox_inches="tight")
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
            # Skip nodes with mesh load less than 0.1%
            if load < 0.1:
                continue
            # Add node to dataframe
            html_nodes.append(dict(
                id=f"{node_id:08X}",
                long_name=long_name,
                packet_count=packet_count,
                load=round(load, 3),
                role=role,
            ))
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
            node_plot.set_xticklabels(rotation=45, ha='right', step=2)
            node_plot.set(
                title=f"{long_name} / {node_id:08X} / Mesh Last: {load:0.2f}%")
            # Calculate mean interval for each packet type of a single node
            delta_t_stats = {}
            for packet_group, packet_details in node_packets.groupby(["port_name"]):
                time_cnt = packet_details["time"].count()
                if time_cnt > 1:
                    packet_details["delta_t"] = packet_details["time"].diff(
                    ).dt.total_seconds()
                    stat = packet_details["delta_t"].agg(
                        ["median", "count"])  # "median", "mean", "min", "max"
                    mean_str = str(datetime.timedelta(
                        seconds=math.ceil(stat["median"])))
                    delta_t_stats[packet_group[0]] = "Median: " + mean_str
            # Add mean value to each packet type in graph
            for ax in node_plot.axes.flat:
                labels = ax.get_yticklabels()
                for label in labels:
                    _, y = label.get_position()
                    txt = label.get_text()
                    h = 0.76 / (len(labels)+1)
                    node_plot.figure.text(
                        1.0, 0.97 - h - (h * y), delta_t_stats.get(txt, "N/A"))
            # Save node statistics graph
            plt.savefig(f"{os.getcwd()}/web/images/{node_id:08X}.png",
                        dpi=100, bbox_inches="tight")
            plt.close()

        # Generate statistical web content
        html_nodes.sort(key=lambda x: x["load"], reverse=True)
        jinja_env = Environment(loader=FileSystemLoader(
            "index.html.j2"), autoescape=True)
        index_template = jinja_env.get_template('')
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
        if os.path.isfile(index_file):
            os.remove(index_file)
        with open(index_file, 'w', encoding='utf-8') as f:
            f.write(html)

    except Exception as e:
        reader.log(
            f"Creating network statistics failed. Error: {e}", level=reader.LOG_ERR)

    finally:
        if database is not None:
            database.close()


def graph(all=False):
    _globals = Globals.getInstance()
    lock = _globals.getLock()
    reader = _globals.getReader()

    sources = []
    destinations = []
    edge_labels = []
    nodes = {}

    try:
        with lock:
            database = sqlite3.connect(
                "network.sqlite3", isolation_level="DEFERRED")
            cur = database.cursor()
            if all:
                res = cur.execute("select * from nodes;")
            else:
                res = cur.execute(
                    "select * from nodes where seen > unixepoch(datetime('now', '-24 hours'));")
            for row in res:
                nodes[f'{row[0]:08X}'] = {
                    "short": row[1], "long": row[2], "seen": row[3]}

            if all:
                res = cur.execute("select * from links;")
            else:
                res = cur.execute(
                    "select * from links where seen > unixepoch(datetime('now', '-24 hours'));")
            for row in res:
                src = row[0]
                dst = row[1]
                snr = row[2]
                sources.append(f'{src:08X}')
                destinations.append(f'{dst:08X}')
                if snr <= -500:
                    edge_labels.append("? dB")
                else:
                    edge_labels.append(f"{snr:0.2f} dB")
            cur.close()

        d3 = d3graph(charge=2000, slider=None, verbose=40,
                     support="Mictronics", collision=3)
        adjmat = vec2adjmat(sources, destinations, weight=None)
        d3.graph(adjmat, cmap="tab20")
        d3.set_path(os.getcwd() + "/web/visualization.html")
        for n in range(len(sources)):
            d3.edge_properties[sources[n], destinations[n]
                               ]['label'] = edge_labels[n]
            d3.edge_properties[sources[n], destinations[n]]['directed'] = True

        for node in d3.node_properties:
            if node in nodes.keys():
                dt = datetime.datetime.fromtimestamp(nodes[node]["seen"])
                last = dt.strftime('%d.%m.%Y %H:%M:%S')
                d3.node_properties[node]['cmap'] = "tab20"
                if nodes[node]["short"] is not None:
                    short = nodes[node]["short"]
                    long = nodes[node]["long"]
                    d3.node_properties[node]['tooltip'] = f"{short}\n{long}\n{last}"
                    d3.node_properties[node]['label'] = short

        d3.show(
            filepath=os.getcwd() + "/web/visualization.html",
            show_slider=False,
            title="Meshtastic Netzwerk Bayern",
            figsize=[None, None],
            showfig=False,
            save_button=False)

    except Exception as e:
        reader.log(
            f"Creating network graph failed. Error: {e}", level=reader.LOG_ERR)

    finally:
        if database is not None:
            database.close()


def logParser():
    # Match port numbers to message identifier string found in debug log
    # There is unfortunately no standard in debug strings
    port_numbers = {
        "unknown": 0,
        "text msg": 1,
        "remotehardware": 2,
        "position": 3,
        "nodeinfo": 4,
        "routing": 5,
        "admin": 6,
        "waypoint msg": 8,
        "telemetry": 67,
        "devicetelemetry": 67,
        "powertelemetry": 67,
        "environmenttelemetry": 67,
        "hostmetrics": 67,
        "traceroute": 70,
    }

    _globals = Globals.getInstance()
    lock = _globals.getLock()
    reader = _globals.getReader()
    ev_run = _globals.getEvRunning()
    if ev_run is None:
        return  # No event to run, exit the thread

    # Connect to database
    try:
        database = sqlite3.connect(
            "network.sqlite3", isolation_level='DEFERRED')
    except Exception as e:
        reader.log(
            f"Connection to database failed. Error: {e}", level=reader.LOG_ERR)
        sys.exit(1)

    # Regular Expressions to match with different debug log line content
    regex_traceroute = r"([0-9abcdef]{8})[ ]?(\(([0-9.-]{0,6})dB\))?"
    regex_node_info = r"user[\s]([\w\W\s]*?), id=0x([0-9abcdef]{8})"
    regex_position = r"POSITION node=(?P<id>[0-9abcdef]{8}).*lat=(?P<lat>[0-9]+).*lon=(?P<lon>[0-9]+)"
    regex_packet_rx = r"Received (?P<type>[A-Za-z ]+) from=(?P<from>[0-9abcdefx]+)[ ,a-z=]+[0-9abcdefx]+[ ,a-z=]+(?P<port_num>[0-9abcdefx]+)"
    regex_decoding = r"(?P<decoding>decoded message|no PSK)"
    regex_role = r"Role (?P<id>[0-9abcdef]{8}) = (?P<role>[0-9]+), HW = (?P<hw>[0-9]+)"

    # Keep track of each received packet type (named module in debug log)
    module_count = _globals.getModuleCount()
    module_count['startlog'] = datetime.datetime.now()
    is_telemetry_packet = False
    telemetry_from_id = 0

    # Parse the Meshtastic debug log
    reader.log(
        f"Log parser started as {reader.__class__.__name__}", level=reader.LOG_INFO)
    while ev_run.is_set():
        for line in reader.poll_read():
            if line is None:
                ev_run.clear()  # Stop the thread if reading serial device failed
                break
            # Store any packet received with source ID, type and timestamp.
            # Used in a nodes packet statistics graph.
            rx_packet = re.search(regex_packet_rx, line)
            if rx_packet is not None:
                # This is why key in port_numbers must be lower case
                type = rx_packet.group("type").lower()
                if type != "routing":
                    telemetry_from_id = int(rx_packet.group("from"), 16)
                    # Ignore broadcast or unknown ID
                    if telemetry_from_id == 0xFFFFFFFF or telemetry_from_id == 0:
                        is_telemetry_packet = False
                        continue
                    if type not in port_numbers.keys():
                        reader.log(
                            f"Unknown packet type: {type} from {telemetry_from_id}", level=reader.LOG_WARNING)

                    else:
                        num = port_numbers[type]
                        if num != 67:
                            # Handle everything except telemetry packets
                            module_count[type] += 1
                            with lock:
                                cur = database.cursor()
                                data = [
                                    {"id": telemetry_from_id, "type": num},]
                                cur.executemany(
                                    "INSERT OR REPLACE INTO packets VALUES(:id, :type, strftime('%s','now'));", data)
                                database.commit()
                                cur.close()
                        else:
                            is_telemetry_packet = True  # Indicate telemetry packet
                            # We need one more line to check which type of telemetry packet it is
                            continue
                    # Save what we counted
                    with lock:
                        _globals.setModuleCount(module_count)

                continue

            # Handle different telemetry packets with port number 67
            if is_telemetry_packet:
                data = None
                if "air_util_tx" in line:
                    module_count['DeviceTelemetry'] += 1
                    data = [{"id": telemetry_from_id, "type": 512},]
                elif "ch1_voltage" in line:
                    module_count['PowerTelemetry'] += 1
                    data = [{"id": telemetry_from_id, "type": 513},]
                elif "barometric_pressure" in line:
                    module_count['EnvironmentTelemetry'] += 1
                    data = [{"id": telemetry_from_id, "type": 514},]
                elif "diskfree" in line:
                    module_count['HostMetrics'] += 1
                    data = [{"id": telemetry_from_id, "type": 515},]
                elif "pm10_standard" in line:
                    module_count['AirQuality'] += 1
                    data = [{"id": telemetry_from_id, "type": 516},]
                elif "heart_bpm" in line:
                    module_count['HealthTelemetry'] += 1
                    data = [{"id": telemetry_from_id, "type": 517},]

                if data is not None:
                    # Store telemetry packet in database
                    with lock:
                        _globals.setModuleCount(module_count)
                        cur = database.cursor()
                        cur.executemany(
                            "INSERT OR REPLACE INTO packets VALUES(:id, :type, strftime('%s','now'));", data)
                        database.commit()
                        cur.close()

                is_telemetry_packet = False
                telemetry_from_id = 0
                continue

            # Handle decoding messages
            decoding = re.search(regex_decoding, line)
            if decoding is not None:
                if decoding.group("decoding") == "decoded message":
                    module_count['decoded'] += 1
                elif decoding.group("decoding") == "no PSK":
                    module_count['encrypted'] += 1
                with lock:
                    _globals.setModuleCount(module_count)
                continue

            # Store names and ID from received node information
            # Used in mesh visualization.
            info = re.search(regex_node_info, line)
            if info is not None:
                hex_id = info.group(2)[-4:].upper()
                id = int(info.group(2), 16)
                # Ignore broadcast or unknown ID
                if id == 0xFFFFFFFF or id == 0:
                    continue
                name = info.group(1)
                name = name.rsplit("/", 1)
                short_name = name[1].strip(" #")
                if short_name is None or short_name == "":
                    short_name = hex_id
                long_name = name[0].strip(" #")
                if long_name is None or long_name == "":
                    long_name = id

                with lock:
                    cur = database.cursor()
                    data = [{"id": id, "shortname": short_name,
                             "longname": long_name},]
                    cur.executemany(
                        "INSERT INTO nodes VALUES(:id, :shortname, :longname, strftime('%s','now'), NULL, NULL, 0, 0, 0) ON CONFLICT(id) DO UPDATE SET shortname=:shortname, longname=:longname, seen=strftime('%s','now');", data)
                    database.commit()
                    cur.close()
                continue

            # Store received nodes position
            pos = re.search(regex_position, line)
            if pos is not None:
                id = int(pos.group(1), 16)
                # Ignore broadcast or unknown ID
                if id == 0xFFFFFFFF or id == 0:
                    continue
                lat = int(pos.group(2), 10) * 1e-7
                lon = int(pos.group(3), 10) * 1e-7
                with lock:
                    cur = database.cursor()
                    data = [{"id": id, "lat": lat, "lon": lon},]
                    cur.executemany(
                        "UPDATE OR IGNORE nodes SET seen = strftime('%s','now'), latitude = :lat, longitude = :lon WHERE id = :id;", data)
                    database.commit()
                    cur.close()
                continue

            # Store received node role and hardware version
            match = re.search(regex_role, line)
            if match is not None:
                id = int(match.group("id"), 16)
                # Ignore broadcast or unknown ID
                if id == 0xFFFFFFFF or id == 0:
                    continue
                role = int(match.group("role"), 10) or 0
                hw = int(match.group("hw"), 10) or 0
                with lock:
                    cur = database.cursor()
                    data = [{"id": id, "role": role, "hw": hw},]
                    cur.executemany(
                        "UPDATE OR IGNORE nodes SET role = :role, hardware = :hw WHERE id = :id;", data)
                    database.commit()
                    cur.close()
                continue

            # Count error -7 (CRC mismatch) for reception quality statistics
            if "error=-7" in line:
                module_count["error7"] += 1
                with lock:
                    _globals.setModuleCount(module_count)
                continue

            # Evaluate all received trace route packets.
            # Packets are split into 2 point connections and stored in database as link between two nodes.
            # Used in mesh visualization.
            if line.startswith("#Start") or line.startswith("|") or line.startswith("#Back"):
                source = None
                dest = None
                snr = None
                nodes = line.split(">")
                for n in range(len(nodes) - 1):
                    source = re.search(regex_traceroute, nodes[n])
                    dest = re.search(regex_traceroute, nodes[n + 1].strip())
                    if dest is not None:
                        if dest.group(3) is not None:
                            snr = float(dest.group(3))
                        else:
                            snr = -500  # Invalid SNR
                        source = int(source.group(1), 16)
                        dest = int(dest.group(1), 16)
                        # Ignore broadcast, unknown ID or equal source-destination
                        if source == 0xFFFFFFFF or dest == 0xFFFFFFFF or source == 0 or dest == 0 or source == dest:
                            continue
                        with lock:
                            cur = database.cursor()
                            data = [
                                {"source": source, "destination": dest, "snr": snr},]
                            cur.executemany(
                                "INSERT OR REPLACE INTO links VALUES(:source, :destination, :snr, strftime('%s','now'));", data)
                            cur.executemany(
                                "INSERT INTO nodes VALUES(:id, NULL, NULL, strftime('%s','now'), NULL, NULL, 0, 0, 0) ON CONFLICT(id) DO UPDATE SET seen=strftime('%s','now');", ({"id": source},))
                            cur.executemany(
                                "INSERT INTO nodes VALUES(:id, NULL, NULL, strftime('%s','now'), NULL, NULL, 0, 0, 0) ON CONFLICT(id) DO UPDATE SET seen=strftime('%s','now');", ({"id": dest},))
                            if n == 0:
                                cur.executemany(
                                    "UPDATE OR IGNORE nodes SET tracestart = tracestart + 1 where id = :id;", ({"id": source},))
                            database.commit()
                            cur.close()
                continue
        # end for entry
    # end while ev_run

    # Close database connection
    if database is not None:
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
    _globals = Globals.getInstance()
    reader = _globals.getReader()
    ev_run = _globals.getEvRunning()
    if ev_run is None:
        return  # No event to run, exit the thread
    schedule.every().hour.at(":10").do(hourlyRunner)
    schedule.every().day.at("11:59:00", "Europe/Berlin").do(dailyRunner)
    schedule.every().day.at("23:59:00", "Europe/Berlin").do(dailyRunner)
    reader.log("Scheduler started", level=reader.LOG_INFO)
    while ev_run.is_set():
        schedule.run_pending()
        time.sleep(1)  # Avoid busy loop


def main():
    """Main program function"""
    _globals = Globals.getInstance()
    _globals.setLock(threading.Lock())
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
        epilog="License GPL-3+ (C) 2025 Michael Wolf, www.mictronics.de",
    )
    _globals.setParser(parser)
    initArgParser()
    args = _globals.getArgs()

    if args.graph == 1:
        graph(all=True)
        sys.exit(0)

    if args.stats == 1:
        statistics()
        sys.exit(0)

    if args.dev is None:
        # Connect to system journal that provides the Meshtasticd debug log
        reader = JournalReader("meshtasticd.service")
    else:
        # Connect to Meshtastic device via serial port
        reader = SerialReader(args.dev)
        if not reader.is_open():
            sys.exit(1)

    _globals.setReader(reader)  # Store reader in globals for other threads

    # The threads we are running
    t = threading.Thread(target=logParser, name="Log Parser")
    t.daemon = True  # Daemon thread will exit when the main program exits
    threads.append(t)
    t = threading.Thread(target=scheduleRunner, name="Scheduler")
    t.daemon = True  # Daemon thread will exit when the main program exits
    threads.append(t)

    _globals.setEvRunning(ev_run)  # Store event in globals for other threads

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
