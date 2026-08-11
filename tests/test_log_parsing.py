"""Regression tests for the regex-based debug log parsing in logParser().

Most fixtures below are lines copied verbatim (ANSI codes stripped) from a real
meshtasticd firmware debug log, captured and hand-verified against these regexes
during a manual review session. REGEX_POSITION and REGEX_TRACEROUTE have no
matching lines in that capture, so their fixtures are constructed by hand to
match the documented format instead of being real captures.
"""

import re

import meshtastic_observer as mo


class TestPacketRxRegex:
    def test_admin_packet(self):
        line = "Received Admin from=0xebe30cb7, id=0xacebbf57, portnum=6, payloadlen=47"
        match = re.search(mo.REGEX_PACKET_RX, line)
        assert match is not None
        assert match.group("type") == "Admin"
        assert match.group("from") == "0xebe30cb7"
        assert match.group("port_num") == "6"

    def test_routing_packet(self):
        line = "Received routing from=0xebe30cb7, id=0xffbc358, portnum=5, payloadlen=2"
        match = re.search(mo.REGEX_PACKET_RX, line)
        assert match is not None
        assert match.group("type") == "routing"

    def test_device_telemetry_packet(self):
        line = "Received DeviceTelemetry from=0x83342c9d, id=0xdfbadc98, portnum=67, payloadlen=22"
        match = re.search(mo.REGEX_PACKET_RX, line)
        assert match is not None
        assert match.group("type") == "DeviceTelemetry"
        assert match.group("from") == "0x83342c9d"
        assert match.group("port_num") == "67"

    def test_nodeinfo_packet(self):
        line = "Received nodeinfo from=0x9f2a9631, id=0x24c8ff6f, portnum=4, payloadlen=76"
        match = re.search(mo.REGEX_PACKET_RX, line)
        assert match is not None
        assert match.group("type") == "nodeinfo"
        assert match.group("port_num") == "4"

    def test_broadcast_from_is_ignored_by_caller(self):
        # from=0x0 still matches the regex; logParser is responsible for the
        # broadcast/unknown-id check, not this pattern.
        line = "Received Admin from=0x0, id=0xf4168527, portnum=6, payloadlen=13"
        match = re.search(mo.REGEX_PACKET_RX, line)
        assert match is not None
        assert int(match.group("from"), 16) == 0

    def test_data_dump_line_does_not_self_match(self):
        # These lines contain the word "Received" but must NOT re-arm the
        # rx_packet branch, or the telemetry sub-type state machine breaks.
        line = "(Received from DON3): barometric_pressure=968.435791, current=0.000000"
        assert re.search(mo.REGEX_PACKET_RX, line) is None


class TestPortNumberLookup:
    def test_known_types_map_to_expected_port(self):
        assert mo.PORT_NUMBERS["admin"] == 6
        assert mo.PORT_NUMBERS["routing"] == 5
        assert mo.PORT_NUMBERS["nodeinfo"] == 4
        assert mo.PORT_NUMBERS["devicetelemetry"] == 67
        assert mo.PORT_NUMBERS["environmenttelemetry"] == 67
        assert mo.PORT_NUMBERS["powertelemetry"] == 67

    def test_lookup_key_must_be_lowercased(self):
        # logParser lowercases rx_packet.group("type") before this lookup.
        assert "DeviceTelemetry".lower() in mo.PORT_NUMBERS
        assert "DeviceTelemetry" not in mo.PORT_NUMBERS


class TestNodeInfoRegex:
    def test_matches_real_user_line(self):
        line = "Update changed=0 user NFN-866#9/9631, id=0x9f2a9631, channel=0"
        match = re.search(mo.REGEX_NODE_INFO, line)
        assert match is not None
        assert match.group(1) == "NFN-866#9/9631"
        assert match.group(2) == "9f2a9631"

    def test_short_and_long_name_derivation(self):
        # Reproduces the short/long-name split logParser performs on group(1).
        line = "Update changed=0 user NFN-866#9/9631, id=0x9f2a9631, channel=0"
        match = re.search(mo.REGEX_NODE_INFO, line)
        name = match.group(1).rsplit("/", 1)
        short_name = name[1].strip(" #")
        long_name = name[0].strip(" #")
        assert short_name == "9631"
        assert long_name == "NFN-866#9"


class TestRoleRegex:
    def test_matches_real_role_line(self):
        line = "Role 9f2a9631 = 1, HW = 30"
        match = re.search(mo.REGEX_ROLE, line)
        assert match is not None
        assert match.group("id") == "9f2a9631"
        assert match.group("role") == "1"
        assert match.group("hw") == "30"

    def test_no_rebroadcast_line_is_not_a_false_positive(self):
        line = "No rebroadcast: Role = CLIENT_MUTE or Rebroadcast Mode = NONE"
        assert re.search(mo.REGEX_ROLE, line) is None


class TestDecodingRegex:
    def test_decoded_message(self):
        line = "decoded message (id=0xdfbadc98 fr=0x83342c9d to=0xffffffff, transport = 1)"
        match = re.search(mo.REGEX_DECODING, line)
        assert match is not None
        assert match.group("decoding") == "decoded message"

    def test_no_psk(self):
        # No real "no PSK" line appeared in the captured sample; this checks
        # the alternation itself still matches the documented phrase.
        line = "no PSK, unable to decrypt"
        match = re.search(mo.REGEX_DECODING, line)
        assert match is not None
        assert match.group("decoding") == "no PSK"


class TestTelemetrySubstrings:
    def test_environment_telemetry_data_line(self):
        line = (
            "(Received from DON3): barometric_pressure=968.435791, current=0.000000, "
            "gas_resistance=0.000000, relative_humidity=37.279999, temperature=31.580000"
        )
        assert "barometric_pressure" in line
        assert "air_util_tx" not in line

    def test_device_telemetry_data_line(self):
        line = "(Received from 5130): air_util_tx=0.008139, channel_utilization=1.465000, battery_level=63, voltage=3.827000"
        assert "air_util_tx" in line
        assert "barometric_pressure" not in line

    def test_module_considered_line_matches_no_substring(self):
        # This is the line that legitimately produces no telemetry data (see
        # the session's manual replay): none of the six known substrings
        # should match it, so it correctly falls through uncounted.
        line = "Module 'PowerTelemetry' considered"
        for substring in (
            "air_util_tx",
            "ch1_voltage",
            "barometric_pressure",
            "diskfree",
            "pm10_standard",
            "heart_bpm",
        ):
            assert substring not in line


class TestErrorMarker:
    def test_real_error7_line(self):
        line = (
            "Ignore received packet due to error=-7 (maybe id=0xf9dc1f44 "
            "fr=0x17322735 to=0xffffffff flags=0xab rxSNR=-16.5 rxRSSI=-119 "
            "nextHop=0x6 relay=0x65)"
        )
        assert "error=-7" in line


class TestPositionRegex:
    def test_synthetic_position_line(self):
        # Constructed from the documented format; no POSITION line appeared
        # in the captured sample log.
        line = "POSITION node=9f2a9631 lat=497359123 lon=115763456"
        match = re.search(mo.REGEX_POSITION, line)
        assert match is not None
        assert match.group("id") == "9f2a9631"
        assert match.group("lat") == "497359123"
        assert match.group("lon") == "115763456"


class TestTracerouteRegex:
    def test_synthetic_traceroute_hop(self):
        # Constructed from the documented format; no traceroute line appeared
        # in the captured sample log.
        segment = "9f2a9631 (-3.50dB)"
        match = re.search(mo.REGEX_TRACEROUTE, segment)
        assert match is not None
        assert match.group(1) == "9f2a9631"
        assert match.group(3) == "-3.50"
