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
"""Process-wide state shared between the log-parser and scheduler threads.

A module is already a singleton (Python caches it after first import), so
plain module-level attributes replace the earlier hand-rolled Singleton class.
"""

args = None
parser = None
lock = None
reader = None
ev_run = None
module_count = {
    "DeviceTelemetry": 0,
    "EnvironmentTelemetry": 0,
    "PowerTelemetry": 0,
    "HostMetrics": 0,
    "AirQuality": 0,
    "HealthTelemetry": 0,
    "StoreForward": 0,
    "ExternalNotificationModule": 0,
    "admin": 0,
    "routing": 0,
    "traceroute": 0,
    "position": 0,
    "nodeinfo": 0,
    "text msg": 0,
    "waypoint msg": 0,
    "startlog": None,
    "error7": 0,
    "decoded": 0,
    "encrypted": 0,
    "time": 0,
}
