"""Deciding which datagrams count as a device reply, and from where."""

from __future__ import annotations


class TestIsEchoResponse:
    """Tests for _is_echo_response."""

    def test_echo_response(self) -> None:
        """Test detection of echoed requests."""
        from custom_components.marstek.discovery import _is_echo_response

        echo = {"method": "Marstek.GetDevice", "params": {"ble_mac": "0"}}
        assert _is_echo_response(echo) is True

    def test_valid_response(self) -> None:
        """Test detection of valid device responses."""
        from custom_components.marstek.discovery import _is_echo_response

        valid = {"result": {"device": "Venus", "ip": "192.168.1.100"}}
        assert _is_echo_response(valid) is False

    def test_response_with_result_and_method(self) -> None:
        """Test response that has both result and method."""
        from custom_components.marstek.discovery import _is_echo_response

        # Has result, so should not be echo even if has method
        response = {
            "result": {"device": "Venus"},
            "method": "Marstek.GetDevice",
            "params": {}
        }
        assert _is_echo_response(response) is False


class TestIsValidDeviceResponse:
    """Tests for _is_valid_device_response."""

    def test_valid_with_device(self) -> None:
        """Test valid response with device field."""
        from custom_components.marstek.discovery import _is_valid_device_response

        response = {"result": {"device": "Venus"}}
        assert _is_valid_device_response(response) is True

    def test_valid_with_ip(self) -> None:
        """Test valid response with ip field."""
        from custom_components.marstek.discovery import _is_valid_device_response

        response = {"result": {"ip": "192.168.1.100"}}
        assert _is_valid_device_response(response) is True

    def test_valid_with_ble_mac(self) -> None:
        """Test valid response with ble_mac field."""
        from custom_components.marstek.discovery import _is_valid_device_response

        response = {"result": {"ble_mac": "AA:BB:CC:DD:EE:FF"}}
        assert _is_valid_device_response(response) is True

    def test_valid_with_wifi_mac(self) -> None:
        """Test valid response with wifi_mac field."""
        from custom_components.marstek.discovery import _is_valid_device_response

        response = {"result": {"wifi_mac": "11:22:33:44:55:66"}}
        assert _is_valid_device_response(response) is True

    def test_invalid_no_result(self) -> None:
        """Test invalid response without result."""
        from custom_components.marstek.discovery import _is_valid_device_response

        response = {"method": "Marstek.GetDevice"}
        assert _is_valid_device_response(response) is False

    def test_invalid_result_not_dict(self) -> None:
        """Test invalid response with non-dict result."""
        from custom_components.marstek.discovery import _is_valid_device_response

        response = {"result": "not a dict"}
        assert _is_valid_device_response(response) is False

    def test_invalid_no_identifiers(self) -> None:
        """Test invalid response without any identifiers."""
        from custom_components.marstek.discovery import _is_valid_device_response

        response = {"result": {"unknown_field": "value"}}
        assert _is_valid_device_response(response) is False


class TestNormalizeIp:
    """Tests for _normalize_ip."""

    def test_no_leading_zeros(self) -> None:
        """Test that a clean IPv4 address is unchanged."""
        from custom_components.marstek.discovery import _normalize_ip

        assert _normalize_ip("192.168.9.92") == "192.168.9.92"

    def test_leading_zero_in_octet(self) -> None:
        """Test that leading zeros are stripped from a single octet."""
        from custom_components.marstek.discovery import _normalize_ip

        assert _normalize_ip("192.168.09.92") == "192.168.9.92"

    def test_multiple_leading_zeros(self) -> None:
        """Test that leading zeros are stripped from all octets."""
        from custom_components.marstek.discovery import _normalize_ip

        assert _normalize_ip("010.001.002.003") == "10.1.2.3"

    def test_invalid_ip_passthrough(self) -> None:
        """Test that invalid IP-like strings are returned unchanged."""
        from custom_components.marstek.discovery import _normalize_ip

        assert _normalize_ip("192.168.invalid.092") == "192.168.invalid.092"

    def test_non_ipv4_passthrough(self) -> None:
        """Test that non-IPv4 hostnames are returned unchanged."""
        from custom_components.marstek.discovery import _normalize_ip

        assert _normalize_ip("marstek.local") == "marstek.local"


def test_is_loopback_host() -> None:
    """Loopback IPs and localhost skip same-port UDP bind."""
    from custom_components.marstek.pymarstek.network import is_loopback_host

    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("192.168.2.37") is False
    assert is_loopback_host("not-an-ip") is False


def test_mac_from_src_compact() -> None:
    """Test compact hex MAC embedded in GetDevice src."""
    from custom_components.marstek.pymarstek.network import mac_from_openapi_src

    assert mac_from_openapi_src("VenusC-AABBCCDDEEFF") == "AA:BB:CC:DD:EE:FF"


def test_mac_from_src_separated() -> None:
    """Test colon-separated MAC embedded in GetDevice src."""
    from custom_components.marstek.pymarstek.network import mac_from_openapi_src

    assert mac_from_openapi_src("VenusC-AA:BB:CC:DD:EE:FF") == "AA:BB:CC:DD:EE:FF"


def test_mac_from_src_missing() -> None:
    """Test src without a MAC returns empty."""
    from custom_components.marstek.pymarstek.network import mac_from_openapi_src

    assert mac_from_openapi_src("VenusC") == ""
    assert mac_from_openapi_src(None) == ""


def test_udp_source_matches_numeric_host() -> None:
    """Unicast GetDevice compares the UDP source to the queried host."""
    from custom_components.marstek.pymarstek.network import udp_source_matches_host

    assert udp_source_matches_host("192.168.1.10", "192.168.1.10") is True
    assert udp_source_matches_host("192.168.1.11", "192.168.1.10") is False


class TestNonObjectDatagrams:
    """A datagram that decodes to a JSON scalar must be noise, not a crash.

    Anything on the LAN can put a datagram on the Open API port, and JSON has
    no rule that a payload be an object. Before these filters were total, one
    such datagram raised ``TypeError`` out of the whole sweep: the scanner
    logged "Scanner discovery failed" and dropped every device found in that
    pass, and the config flow's discovery step never returned a device list.
    """

    def test_echo_filter_rejects_non_objects(self) -> None:
        """_is_echo_response says False for every non-object payload."""
        from custom_components.marstek.discovery import _is_echo_response

        for payload in (5, 5.5, True, False, None, "text", [1, 2]):
            assert _is_echo_response(payload) is False

    def test_valid_filter_rejects_non_objects(self) -> None:
        """_is_valid_device_response says False for every non-object payload."""
        from custom_components.marstek.discovery import _is_valid_device_response

        for payload in (5, 5.5, True, False, None, "text", [1, 2]):
            assert _is_valid_device_response(payload) is False
