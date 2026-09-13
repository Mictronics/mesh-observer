# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python script that connects to a Meshtastic node over its TCP API, logs the mesh traffic it
sees into a SQLite database, and periodically renders that data into a static HTML site (charts,
network graph, per-node stats) which can optionally be uploaded via FTP -- the same job this
project always did.

Meshtastic firmware 2.8.x removed MQTT JSON publishing entirely, and the node's TCP API only
accepts one client connection at a time. So besides being a passive-ish observer, this project is
now also the sole holder of that one connection, and **repeats it out** over both the TCP and HTTP
Meshtastic API surfaces to whichever other clients need live mesh data (`meshtastic2hass`,
`meshtastic-powered-vue`, and any future Meshtastic API client) -- using the real wire protocol(s),
not an invented JSON format, so those clients need no changes of their own. This is a deliberate,
documented exception to the project's earlier "never uses the Meshtastic API" design constraint,
made because that constraint's original justification (MQTT JSON existed as an alternative) no
longer holds upstream.

## Setup and running

```bash
# Virtual environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Create the SQLite database from the schema (network.sqlite3 is gitignored)
python3 create_database.py
```

Run modes (mutually exclusive):

```bash
# Connect to the node (see tcp_credentials.py), write to the database, and
# repeat the connection out over both repeater ports until stopped
python3 meshtastic_observer.py

# One-shot: (re)generate web/visualization.html from all-time DB data, then exit
python3 meshtastic_observer.py -g

# One-shot: (re)generate web/index.html + stat graphs from DB, then exit
python3 meshtastic_observer.py -s

# Print the version string (1.0.0) and exit
python3 meshtastic_observer.py --version
```

(`-g`/`-s` also have long forms `--graph`/`--stats`.)

Create `tcp_credentials.py` (gitignored, not present in repo) exporting `__hostname__`/`__port__`
(the node's TCP API) and `__repeater_tcp_port__`/`__repeater_http_port__` (the ports this app's own
repeater binds to) -- see README.md for the exact template.

For FTP upload of the generated `web/` folder, create `ftp_credentials.py` (gitignored, not present
in repo) exporting `__hostname__`, `__username__`, `__password__`, `__local_folder__`,
`__remote_folder__` -- see README.md for the exact template. Without it, `ftp_upload()` calls in
`hourlyRunner`/`dailyRunner` will fail; comment them out if FTP isn't wanted.

There is no linter invocation or build step beyond running the script directly. A `pytest` suite
under `tests/` covers the packet-to-database-write logic (`tests/test_tcp_packet_handling.py`),
using synthetic packet dicts shaped like real decoded Meshtastic traffic -- no live connection
needed. Trunk (`.trunk/trunk.yaml`) is configured with black, ruff, isort, bandit, markdownlint,
prettier -- run `trunk check` / `trunk fmt` if trunk is installed, but there's no CI wiring to rely
on in this repo.

## Architecture

Everything runs from **`meshtastic_observer.py`**, launched as two daemon threads plus the main thread:

- **TCP listener thread** (`tcpListener`) -- the core ingestion loop. Subscribes once to the
  `meshtastic` Python library's pypubsub topic `"meshtastic.receive"` (which, thanks to pypubsub's
  topic hierarchy, catches every packet type regardless of sub-topic), and for each decoded packet
  calls `_handle_tcp_packet()` to write into `network.sqlite3` (`nodes`, `links`, `packets` tables)
  and increments the in-memory `Globals.module_count` counters used for packets-per-hour
  statistics. Note that the port-number-to-string mapping (`decoded["portnum"]`) and telemetry
  sub-type discrimination (`_classify_telemetry()`, keyed on which oneof field -- `deviceMetrics`,
  `environmentMetrics`, etc. -- is present in the decoded `Telemetry` message) now come straight
  from the library's own protobuf decoding, not from reverse-engineering debug log text; the
  synthetic port numbers 512-517 this project invented to disambiguate telemetry sub-types on the
  wire's single `TELEMETRY_APP` port (67) are unchanged (see Database, below). The gateway node's
  own packets update node metadata (`_upsert_node`) but are excluded from the `packets` table (see
  `is_own` in `_handle_tcp_packet()`), so `statistics()`'s packet-rate charts reflect only traffic
  that actually went out over LoRa, not connected-API chatter.
- **`tcpListener` also starts the repeater** (`repeater_core.py` + `tcp_repeater.py` +
  `http_repeater.py`) against the same shared `TCPInterface`, since the node only allows one
  client -- see Reader/repeater abstraction below.
- **Scheduler thread** (`scheduleRunner`) -- uses the `schedule` library to run `hourlyRunner`
  (lightweight stats + FTP upload of just the two hourly PNGs) at `:10` past each hour, and
  `dailyRunner` (full graph regeneration + full stats + full site FTP upload) at 11:59 and 23:59
  Europe/Berlin.
- **Main thread** -- argument parsing, connects `TcpReader` to the node, signal handling
  (SIGINT/SIGABRT/SIGTERM clear a shared `threading.Event` to stop both threads cleanly, including
  interrupting `TcpReader`'s connect-retry backoff loop), then joins the threads.

**Reader/repeater abstraction**: `TcpReader` (`tcp_reader.py`, wraps
`meshtastic.tcp_interface.TCPInterface`) owns the single upstream connection to the node -- the
node's TCP API only accepts one client at a time (verified against firmware
`src/mesh/api/ServerAPI.cpp`), so this is the one connection shared by the DB writer and the
repeater. On a hard failure the library's own reader thread exits for good and does not reconnect
itself, so `TcpReader` owns connect/reconnect-with-backoff and swaps `self.iface` out from under
callers -- always read `reader.iface` fresh, never cache the `TCPInterface` instance. It exposes
the same informal `is_open()`/`close()`/`log()` interface the old readers did.

`repeater_core.py`'s `RepeaterCore` is the transport-agnostic session/handshake/fan-out logic
shared by both repeater adapters: it synthesizes a full Meshtastic config handshake for each new
downstream client from the shared `TCPInterface`'s already-cached state (`iface.myInfo`,
`.nodesByNum`, `.localNode.channels/.localConfig/.moduleConfig` -- the Python client doesn't care
what order handshake messages arrive in, only that `config_complete_id` eventually shows up, so
order and completeness here are best-effort, not a strict re-implementation of the firmware's own
ordered state machine) -- **except one nonce, which is not best-effort**: `want_config_id ==
SPECIAL_NONCE_ONLY_NODES` (69421, firmware `PhoneAPI.h`) must get *only* `node_info` messages, no
`my_info`. Some clients run a two-stage handshake using this exact nonce to fetch just the node
list (e.g. Meshtastic Android's `MeshConfigFlowManagerImpl`); their state machine resets to stage
1 on any `my_info`, so replaying the full bundle for this nonce silently strands them mid-connect
with no visible error. `_build_node_messages()` vs `_build_config_messages()` in `repeater_core.py`
is this split -- don't collapse it back into one path.

Admin requests (`ADMIN_APP` packets) need a per-node session passkey the real firmware issues in
response to a specific bootstrap request; a repeater client's own synthesized handshake never goes
through that exchange, so its admin requests would otherwise be silently rejected. `TcpReader`
proactively bootstraps our own passkey (`ensure_admin_session_key()`), and `RepeaterCore`
substitutes it into any downstream client's outgoing admin packet (`_rewrite_admin_passkey()`)
before forwarding upstream.

It forwards any other `ToRadio` message straight to the upstream socket, and
fans out each live decoded packet (re-wrapping the already-parsed `MeshPacket` object, no
hand-rolled protobuf encode/decode needed) to every connected session's queue. `tcp_repeater.py`
is a thin `socketserver.ThreadingTCPServer` adapter speaking the real framed wire protocol (2 magic
bytes + 2-byte length + protobuf; see firmware `src/mesh/StreamAPI.cpp`) so
`meshtastic.tcp_interface.TCPInterface`-based clients (`meshtastic2hass`, ...) connect unmodified.
`http_repeater.py` is a thin `http.server.ThreadingHTTPServer` adapter implementing the firmware's
HTTP REST surface (`GET`/`PUT /api/v1/{from,to}radio`, unframed raw protobuf, CORS enabled; see
firmware `src/mesh/http/ContentHandler.cpp`) so browser clients using `@meshtastic/transport-http`
(`meshtastic-powered-vue`) connect unmodified too -- like the real firmware's own HTTP API, this
adapter is a single implicit session shared by whoever's talking to it, not one session per
request. Both repeater surfaces grant full read/write access to the mesh (any `ToRadio` except
`want_config_id` passes through unmodified -- `sendText`, admin/config changes, everything),
equivalent to a client plugging directly into the node; there's no additional auth layer, matching
the node's own (unauthenticated) TCP/HTTP APIs today.

**Verified working**: `meshtastic2hass` (TCP), the official Meshtastic Android app (TCP), and
`meshtastic-powered-vue` (HTTP) all connect and operate correctly through the repeater. The HTTP
side needs one thing outside this repo: `meshtastic-powered-vue`'s HTTP transport always calls
`/api/v1/{from,to}radio` at the *site root* of whatever host/TLS it's given (it drops any path
component), so if the app is served from a subpath behind a reverse proxy (e.g. nginx `alias`-ing
`/meshtastic/` to the built site), that proxy also needs a root-level location proxying
`/api/v1/` to this repeater's HTTP port (`__repeater_http_port__`, default 4404) -- otherwise the
proxy answers those requests itself (404/405) instead of forwarding them, which surfaces in the
browser console as connect failures that look CORS-related but aren't (`http_repeater.py` already
sets the right CORS headers).

**Shared state**: `globals.py`'s `Globals` is a singleton holding cross-thread state that would
otherwise need to be threaded through every function -- the argparse parser/args, the active
reader instance, the `threading.Lock` guarding all SQLite writes, the `threading.Event` used to
stop threads, and the `module_count` dict (per-packet-type counters plus decode/encrypt/error
counts, reset every 24h). All SQLite access -- from any thread, including the one-shot `-g`/`-s`
invocations -- must hold `Globals.getInstance().getLock()`.

**Database** (`network.sqlite3`, schema in `network.sqlite3.sql`) -- unchanged by the TCP/repeater
rework:
- `nodes` -- one row per known node ID, with names, last-seen timestamp, position, role, hardware,
  and a `tracestart` counter.
- `links` -- directed edges between node IDs seen in traceroute output, with SNR; a trigger
  auto-deletes rows older than 24h on every insert. Traceroute now writes the full hop-by-hop chain
  (real node numbers from the decoded `RouteDiscovery`, not just the two endpoints), since the TCP
  API -- unlike the old MQTT JSON format -- gives resolvable node numbers for the whole route.
- `packets` -- one row per received packet (source, port-type int, timestamp); a trigger
  auto-deletes rows older than 7 days on every insert. Both retention triggers mean historical
  depth is inherently limited -- long-range stats must be computed before data ages out.
- `packet_types` -- static lookup table mapping Meshtastic port numbers to display names (note
  ports 512-517 are *not* real Meshtastic port numbers; they're synthetic values this project
  invented to disambiguate the different telemetry sub-types that all arrive on port 67).
- `ViewPackets` -- join of the above three, used as the single source for the stats/plotting code
  in `statistics()`.

**Web output** (`web/` -- mostly gitignored, regenerated on each run): `meshtastic_observer.py`'s
`statistics()` builds several matplotlib/seaborn charts (`stats.png`, `decoding.png`,
`hourly_heatmap.png`, `daily.png`, `weekly.png`, plus one PNG per active node under
`web/images/`) and renders `index.html.j2` (Jinja2) into `web/index.html`. `graph()` builds an
interactive `d3graph` network visualization at `web/visualization.html`. Text in `index.html.j2`
and the chart labels are German, matching the deployment target audience -- keep that consistent
when editing.

**Not part of the runtime path**: `gini.py` and `seaborn_test.py` are gitignored scratch/experiment
scripts, not invoked by `meshtastic_observer.py`.

## Deployment

Intended to run as a systemd service (`meshobserver.service`) under a dedicated `meshtasticd` user,
from `/opt/meshtastic_observer` with the venv's own interpreter. Update the hardcoded paths in that
unit file before installing it elsewhere. The repeater's TCP and HTTP ports need to be reachable
from whatever hosts run its downstream clients (LAN firewall rules, etc.) -- they carry the same
unauthenticated full read/write access to the mesh as the node's own APIs, so don't expose them
more widely than the node itself would have been.
