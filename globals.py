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
# along with meshtastic2hass. If not, see http://www.gnu.org/licenses/.
#
class Globals:
    """Globals class is a Singleton."""

    __instance = None

    @staticmethod
    def getInstance():
        """Get an instance of the Globals class."""
        if Globals.__instance is None:
            Globals()
        return Globals.__instance

    def __init__(self):
        """Constructor for the Globals CLass"""
        if Globals.__instance is not None:
            raise Exception("This class is a singleton")
        else:
            Globals.__instance = self
        self.args = None
        self.parser = None
        self.lock = None
        self.module_count = {
            'DeviceTelemetry': 0,
            'EnvironmentTelemetry': 0,
            'PowerTelemetry': 0,
            'HostMetrics': 0,
            'AirQuality': 0,
            'HealthTelemetry': 0,
            'StoreForward': 0,
            'ExternalNotificationModule': 0,
            'Admin': 0,
            'routing': 0,
            'traceroute': 0,
            'position': 0,
            'nodeinfo': 0,
            'text msg': 0,
            'startlog': None,
            'error7': 0,
        }

    def reset(self):
        """Reset all of our globals. If you add a member, add it to this method, too."""
        self.args = None
        self.parser = None
        self.lock = None
        self.module_count = {
            'DeviceTelemetry': 0,
            'EnvironmentTelemetry': 0,
            'PowerTelemetry': 0,
            'HostMetrics': 0,
            'AirQuality': 0,
            'HealthTelemetry': 0,
            'StoreForward': 0,
            'ExternalNotificationModule': 0,
            'Admin': 0,
            'traceroute': 0,
            'routing': 0,
            'position': 0,
            'nodeinfo': 0,
            'text msg': 0,
            'startlog': None,
            'error7': 0,
        }

    # setters
    def setArgs(self, args):
        """Set the args"""
        self.args = args

    def setParser(self, parser):
        """Set the parser"""
        self.parser = parser

    def setLock(self, lock):
        """Set the lock"""
        self.lock = lock

    def setModuleCount(self, module_count):
        """Set the module counter"""
        self.module_count = module_count

    # getters
    def getArgs(self):
        """Get args"""
        return self.args

    def getParser(self):
        """Get parser"""
        return self.parser

    def getLock(self):
        """Get the lock"""
        return self.lock
    
    def getModuleCount(self):
        """Get the module counter"""
        return self.module_count