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
"""HTTP adapter for the Meshtastic API repeater (repeater_core.py).

Implements the firmware's HTTP REST API surface -- GET/PUT
/api/v1/{from,to}radio, raw unframed protobuf bytes, CORS enabled (see
firmware src/mesh/http/ContentHandler.cpp) -- so browser clients like
meshtastic-powered-vue's @meshtastic/transport-http work unmodified. Like the
real firmware's own HTTP API, this is a single implicit session shared by
whichever browser talks to it, not one session per request.
"""
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, PUT, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class _Handler(BaseHTTPRequestHandler):
    def _start_response(self, status):
        self.send_response(status)
        self.send_header("Content-Type", "application/x-protobuf")
        for key, value in CORS_HEADERS.items():
            self.send_header(key, value)

    def do_OPTIONS(self):
        self._start_response(204)
        self.end_headers()

    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path != "/api/v1/fromradio":
            self._start_response(404)
            self.end_headers()
            return
        drain_all = parse_qs(parts.query).get("all", ["false"])[0] == "true"
        session = self.server.session
        body = b""
        try:
            while True:
                msg = session.queue.get_nowait()
                body += msg.SerializeToString()
                if not drain_all:
                    break
        except queue.Empty:
            pass
        self._start_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        if urlsplit(self.path).path != "/api/v1/toradio":
            self._start_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self.server.core.handle_to_radio(self.server.session, body)
        # Real firmware echoes the received bytes back; harmless and matches it.
        self._start_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # avoid duplicating the app's own log() convention via stderr


class HttpRepeaterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host, port, core):
        self.core = core
        self.session = core.new_session()
        super().__init__((host, port), _Handler)
