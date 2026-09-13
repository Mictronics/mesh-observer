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
"""TCP socket adapter for the Meshtastic API repeater (repeater_core.py).

Speaks the same framed wire protocol as a real Meshtastic node's TCP API
(2 magic bytes + 2-byte big-endian length + protobuf payload; see firmware
src/mesh/StreamAPI.cpp), so any client using e.g.
meshtastic.tcp_interface.TCPInterface connects exactly as it would to the
real node.
"""
import socket
import socketserver
import threading

from repeater_core import START1, START2, frame

HEADER_LEN = 4


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        core = self.server.core
        reader = core.reader
        session = core.new_session()
        reader.log(f"Repeater: TCP client connected from {self.client_address[0]}", level=reader.LOG_INFO)
        writer = threading.Thread(target=self._writer_loop, args=(session,), daemon=True)
        writer.start()
        reason = "closed"
        try:
            self._reader_loop(core, session)
        except OSError as ex:
            # Covers ConnectionResetError/BrokenPipeError/TimeoutError etc. -- a
            # client dropping its connection is a normal, expected event, not a
            # bug; just log it instead of letting socketserver print a traceback.
            reason = str(ex)
        finally:
            core.remove_session(session)
            session.queue.put(None)  # unblock the writer loop so it can exit
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            reader.log(
                f"Repeater: TCP client {self.client_address[0]} disconnected ({reason})",
                level=reader.LOG_INFO,
            )

    def _writer_loop(self, session):
        while True:
            msg = session.queue.get()
            if msg is None:
                return
            try:
                self.request.sendall(frame(msg.SerializeToString()))
            except OSError:
                return

    def _reader_loop(self, core, session):
        buf = b""
        while True:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            buf += chunk
            while True:
                start = buf.find(bytes([START1, START2]))
                if start == -1:
                    buf = b""
                    break
                if len(buf) < start + HEADER_LEN:
                    buf = buf[start:]
                    break
                length = (buf[start + 2] << 8) | buf[start + 3]
                end = start + HEADER_LEN + length
                if len(buf) < end:
                    buf = buf[start:]
                    break
                core.handle_to_radio(session, buf[start + HEADER_LEN : end])
                buf = buf[end:]


class TcpRepeaterServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host, port, core):
        self.core = core
        super().__init__((host, port), _Handler)
