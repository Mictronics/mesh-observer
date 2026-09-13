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
"""Transport-agnostic Meshtastic API repeater core, shared by tcp_repeater.py
and http_repeater.py.

The node's own TCP/HTTP APIs each only accept one client at a time (see
firmware src/mesh/api/ServerAPI.cpp and src/mesh/http/ContentHandler.cpp), so
meshtastic_observer holds the single upstream connection (via TcpReader) and
repeats it out to as many downstream clients as connect here -- using the
real Meshtastic wire protocol, so those clients (meshtastic2hass,
meshtastic-powered-vue, ...) need no changes of their own.
"""
import queue
import threading

from google.protobuf import json_format
from meshtastic.protobuf import admin_pb2, config_pb2, mesh_pb2, module_config_pb2, portnums_pb2
from pubsub import pub

START1 = 0x94
START2 = 0xC3


def frame(payload: bytes) -> bytes:
    """Wrap payload in the wire framing shared by both repeater adapters
    (2 magic bytes + 2-byte big-endian length; see firmware StreamAPI.cpp)."""
    length = len(payload)
    return bytes([START1, START2, (length >> 8) & 0xFF, length & 0xFF]) + payload

# Firmware PhoneAPI.h: a want_config_id nonce meaning "skip everything except
# node_info" -- some clients (e.g. Meshtastic Android) rely on this to fetch
# just the node list without re-triggering their own handshake-progress state
# machine, which reacts to a fresh my_info by resetting itself.
SPECIAL_NONCE_ONLY_NODES = 69421


class Session:
    """One connected (or, for HTTP, the one implicit) downstream client.

    `queue` holds pending FromRadio messages waiting to be sent to the client.
    """

    def __init__(self):
        self.queue = queue.Queue()


class RepeaterCore:
    def __init__(self, reader):
        self.reader = reader  # TcpReader -- reader.iface is the shared upstream TCPInterface
        self._sessions = set()
        self._sessions_lock = threading.Lock()
        self._upstream_lock = threading.Lock()
        pub.subscribe(self._on_packet, "meshtastic.receive")

    def new_session(self):
        session = Session()
        with self._sessions_lock:
            self._sessions.add(session)
        return session

    def remove_session(self, session):
        with self._sessions_lock:
            self._sessions.discard(session)

    def _on_packet(self, packet, interface):
        raw = packet.get("raw")
        if raw is None:
            return
        from_radio = mesh_pb2.FromRadio(packet=raw)
        with self._sessions_lock:
            sessions = list(self._sessions)
        for session in sessions:
            session.queue.put(from_radio)

    def handle_to_radio(self, session, raw_bytes):
        """Decode one (unframed) ToRadio message from a client and act on it."""
        to_radio = mesh_pb2.ToRadio()
        try:
            to_radio.ParseFromString(raw_bytes)
        except Exception as ex:
            self.reader.log(
                f"Repeater: malformed ToRadio from client: {ex}", level=self.reader.LOG_WARNING
            )
            return
        kind = to_radio.WhichOneof("payload_variant")
        if kind == "want_config_id":
            self._start_config(session, to_radio.want_config_id)
            return

        detail = ""
        if kind == "packet":
            p = to_radio.packet
            if p.HasField("decoded"):
                portnum = portnums_pb2.PortNum.Name(p.decoded.portnum)
                if portnum == "ADMIN_APP":
                    raw_bytes = self._rewrite_admin_passkey(to_radio) or raw_bytes
            else:
                portnum = "<encrypted>"
            detail = f" (to={p.to:08x}, portnum={portnum})"
        self.reader.log(
            f"Repeater: client sent ToRadio.{kind}{detail}, forwarding upstream", level=self.reader.LOG_DEBUG
        )
        self._forward_upstream(raw_bytes)

    def _rewrite_admin_passkey(self, to_radio):
        """A repeater client's own admin request never went through the real
        session-passkey bootstrap round trip with the node (that only ever
        happened for our own shared upstream connection) -- substitute our
        cached passkey for that target node so the node's admin module
        accepts it. Returns modified bytes, or None to just forward as-is
        (e.g. no cached passkey yet, or nothing to rewrite).
        """
        iface = self.reader.iface
        if iface is None:
            return None
        p = to_radio.packet
        admin = admin_pb2.AdminMessage()
        try:
            admin.ParseFromString(p.decoded.payload)
        except Exception:
            return None
        node = iface.nodesByNum.get(p.to)
        passkey = node.get("adminSessionPassKey") if node else None
        self.reader.log(
            f"Repeater: admin request variant={admin.WhichOneof('payload_variant')} "
            f"channel={p.channel} pki_encrypted={p.pki_encrypted} want_response={p.decoded.want_response} "
            f"hop_limit={p.hop_limit} id={p.id:08x}; have cached passkey: {bool(passkey)}, "
            f"len={len(passkey) if passkey else 0}",
            level=self.reader.LOG_DEBUG,
        )
        if not passkey:
            if iface.myInfo is not None and p.to == iface.myInfo.my_node_num:
                self.reader.ensure_admin_session_key()  # best-effort for next time
            return None
        admin.session_passkey = passkey
        p.decoded.payload = admin.SerializeToString()
        return to_radio.SerializeToString()

    def _forward_upstream(self, raw_bytes):
        iface = self.reader.iface
        if iface is None or iface.socket is None:
            return
        try:
            with self._upstream_lock:
                iface.socket.sendall(frame(raw_bytes))
        except Exception as ex:
            self.reader.log(f"Repeater: failed forwarding to node: {ex}", level=self.reader.LOG_WARNING)

    def _start_config(self, session, nonce):
        """Synthesize a config handshake for one client from the shared
        TCPInterface's already-cached state. The real Python client only waits
        for config_complete_id (see mesh_interface.py's HasField-based
        dispatch) -- order doesn't matter, and sections we can't build cleanly
        are simply skipped rather than blocking the handshake. EXCEPT:
        SPECIAL_NONCE_ONLY_NODES (see firmware PhoneAPI.h) is a real client
        contract, not just a nice-to-have -- some clients (Meshtastic Android's
        two-stage handshake) send it specifically to mean "only node_info,
        nothing else" and will discard the resulting config_complete_id if
        my_info arrives again in the same batch (a fresh my_info resets their
        handshake state machine back to stage 1). Sending the full bundle here
        anyway silently breaks those clients with no visible error.
        """
        iface = self.reader.iface
        messages = []
        if iface is not None:
            # ponytail: iface's cached node/channel/config state is mutated by
            # the upstream library's own thread without a lock we can take;
            # a torn read here is possible but rare on a home network. Upgrade
            # path if it ever bites: ask the library maintainers for a lock,
            # or snapshot state on every update instead of reading live.
            try:
                if nonce == SPECIAL_NONCE_ONLY_NODES:
                    messages.extend(self._build_node_messages(iface))
                else:
                    messages.extend(self._build_config_messages(iface))
            except Exception as ex:
                self.reader.log(
                    f"Repeater: error building config handshake: {ex}", level=self.reader.LOG_WARNING
                )
        messages.append(mesh_pb2.FromRadio(config_complete_id=nonce))
        # ponytail: some official clients' connect flow reportedly waits on a
        # queueStatus after config_complete_id; we have no real queue depth to
        # report, so this just announces "queue empty" rather than tracking
        # actual outstanding sends. Upgrade path: track real in-flight count
        # per session if a client turns out to need accurate free/maxlen.
        messages.append(mesh_pb2.FromRadio(queueStatus=mesh_pb2.QueueStatus(res=0, free=100, maxlen=100)))
        self.reader.log(
            f"Repeater: sending handshake, {len(messages)} messages (nonce={nonce})",
            level=self.reader.LOG_INFO,
        )
        for msg in messages:
            session.queue.put(msg)

    def _build_node_messages(self, iface):
        """Only node_info messages -- see SPECIAL_NONCE_ONLY_NODES handling
        in _start_config() for why this must stay separate from the full
        config bundle rather than just being a subset of it."""
        messages = []
        for node in list(iface.nodesByNum.values()):
            node_info = mesh_pb2.NodeInfo()
            try:
                # ignore_unknown_fields: the library adds convenience keys
                # (e.g. position.latitude/.longitude floats) alongside the
                # real protobuf field names when caching nodes.
                json_format.ParseDict(node, node_info, ignore_unknown_fields=True)
            except Exception:
                continue
            messages.append(mesh_pb2.FromRadio(node_info=node_info))
        return messages

    def _build_config_messages(self, iface):
        messages = []
        if iface.myInfo is not None:
            messages.append(mesh_pb2.FromRadio(my_info=iface.myInfo))

        messages.extend(self._build_node_messages(iface))

        for channel in getattr(iface.localNode, "channels", None) or []:
            messages.append(mesh_pb2.FromRadio(channel=channel))

        local_config = iface.localNode.localConfig
        for field in local_config.DESCRIPTOR.fields:
            if field.name == "version" or not local_config.HasField(field.name):
                continue
            cfg = config_pb2.Config()
            getattr(cfg, field.name).CopyFrom(getattr(local_config, field.name))
            messages.append(mesh_pb2.FromRadio(config=cfg))

        local_module_config = iface.localNode.moduleConfig
        for field in local_module_config.DESCRIPTOR.fields:
            if field.name == "version" or not local_module_config.HasField(field.name):
                continue
            mod_cfg = module_config_pb2.ModuleConfig()
            getattr(mod_cfg, field.name).CopyFrom(getattr(local_module_config, field.name))
            messages.append(mesh_pb2.FromRadio(moduleConfig=mod_cfg))

        return messages
