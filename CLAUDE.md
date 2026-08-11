# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python script that passively monitors a Meshtastic mesh network by tailing a node's debug log — either from a locally running `meshtasticd` native service (via the systemd journal) or from a Meshtastic device connected over serial (USB). It never uses the Meshtastic API and never transmits packets; it works purely by regex-parsing debug log lines. Parsed data is stored in a SQLite database, and the script periodically renders that data into a static HTML site (charts, network graph, per-node stats) which can optionally be uploaded via FTP.

## Setup and running

```bash
# System dependency for the journal reader
sudo apt install libsystemd-journal-dev

# Virtual environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Create the SQLite database from the schema (network.sqlite3 is gitignored)
python3 create_database.py
```

Run modes (mutually exclusive):

```bash
# Passive monitor via serial device
python3 meshtastic_observer.py --dev /dev/ttyUSB0

# Passive monitor via local meshtasticd journal (default, no --dev)
python3 meshtastic_observer.py

# One-shot: (re)generate web/visualization.html from all-time DB data, then exit
python3 meshtastic_observer.py -g

# One-shot: (re)generate web/index.html + stat graphs from DB, then exit
python3 meshtastic_observer.py -s
```

`meshtasticd` must have its journal log level set to `debug` for the journal reader to see anything (see README.md for the `journald.conf` / `meshtasticd config.yaml` settings needed on Linux, since debug logging can grow the journal quickly).

For FTP upload of the generated `web/` folder, create `ftp_credentials.py` (gitignored, not present in repo) exporting `__hostname__`, `__username__`, `__password__`, `__local_folder__`, `__remote_folder__` — see README.md for the exact template. Without it, `ftp_upload()` calls in `hourlyRunner`/`dailyRunner` will fail; comment them out if FTP isn't wanted.

There is no test suite, linter invocation, or build step beyond running the script directly. Trunk (`.trunk/trunk.yaml`) is configured with black, ruff, isort, bandit, markdownlint, prettier — run `trunk check` / `trunk fmt` if trunk is installed, but there's no CI wiring to rely on in this repo.

## Architecture

Everything runs from **`meshtastic_observer.py`**, launched as two daemon threads plus the main thread:

- **Log Parser thread** (`logParser`) — the core ingestion loop. Pulls lines from a reader (`poll_read()`), matches them against a set of hardcoded regexes (packet rx, node info, position, role/hardware, decoding status, traceroute hops), and writes results into `network.sqlite3` (`nodes`, `links`, `packets` tables). Also increments in-memory counters in `Globals.module_count` for the packets-per-hour statistics. Note the debug log format is undocumented/unstable upstream — the port-number-to-string mapping in `port_numbers` and the telemetry sub-type sniffing (matching on substrings like `air_util_tx`, `ch1_voltage`, `barometric_pressure`) are reverse-engineered from observed log lines, not a spec.
- **Scheduler thread** (`scheduleRunner`) — uses the `schedule` library to run `hourlyRunner` (lightweight stats + FTP upload of just the two hourly PNGs) at `:10` past each hour, and `dailyRunner` (full graph regeneration + full stats + full site FTP upload) at 11:59 and 23:59 Europe/Berlin.
- **Main thread** — argument parsing, reader selection, signal handling (SIGINT/SIGABRT/SIGTERM clear a shared `threading.Event` to stop both threads cleanly), then joins the threads.

**Reader abstraction**: `JournalReader` (`journal_reader.py`, wraps `systemd.journal`) and `SerialReader` (`serial_reader.py`, wraps `pyserial`) both expose the same informal interface — `poll_read()` (returns a list of log lines, or `[None]` on fatal error to signal the parser to stop), `log(message, level)`, and `close()`. `meshtastic_observer.py` picks one based on whether `--dev` was passed and treats them interchangeably; if you add a new source of debug lines, match this interface.

**Shared state**: `globals.py`'s `Globals` is a singleton holding cross-thread state that would otherwise need to be threaded through every function — the argparse parser/args, the active reader instance, the `threading.Lock` guarding all SQLite writes, the `threading.Event` used to stop threads, and the `module_count` dict (per-packet-type counters plus decode/encrypt/error counts, reset every 24h). All SQLite access — from any thread, including the one-shot `-g`/`-s` invocations — must hold `Globals.getInstance().getLock()`.

**Database** (`network.sqlite3`, schema in `network.sqlite3.sql`):
- `nodes` — one row per known node ID, with names, last-seen timestamp, position, role, hardware, and a `tracestart` counter.
- `links` — directed edges between node IDs seen in traceroute output, with SNR; a trigger auto-deletes rows older than 24h on every insert.
- `packets` — one row per received packet (source, port-type int, timestamp); a trigger auto-deletes rows older than 7 days on every insert. Both retention triggers mean historical depth is inherently limited — long-range stats must be computed before data ages out.
- `packet_types` — static lookup table mapping Meshtastic port numbers to display names (note ports 512–517 are *not* real Meshtastic port numbers; they're synthetic values this project invented to disambiguate the different telemetry sub-types that all arrive on port 67).
- `ViewPackets` — join of the above three, used as the single source for the stats/plotting code in `statistics()`.

**Web output** (`web/` — mostly gitignored, regenerated on each run): `meshtastic_observer.py`'s `statistics()` builds several matplotlib/seaborn charts (`stats.png`, `decoding.png`, `hourly_heatmap.png`, `daily.png`, `weekly.png`, plus one PNG per active node under `web/images/`) and renders `index.html.j2` (Jinja2) into `web/index.html`. `graph()` builds an interactive `d3graph` network visualization at `web/visualization.html`. Text in `index.html.j2` and the chart labels are German, matching the deployment target audience — keep that consistent when editing.

**Not part of the runtime path**: `gini.py` and `seaborn_test.py` are gitignored scratch/experiment scripts, not invoked by `meshtastic_observer.py`.

## Deployment

Intended to run as a systemd service (`meshobserver.service`) under a dedicated `meshtasticd` user, from `/opt/meshtastic_observer` with the venv's own interpreter. Update the hardcoded paths in that unit file before installing it elsewhere.
