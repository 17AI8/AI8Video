from __future__ import annotations

import unittest
from unittest.mock import patch

from ai8video.integrations import http_client


class AI8VideoHttpClientTest(unittest.TestCase):
    def tearDown(self) -> None:
        http_client.detect_physical_local_address.cache_clear()

    def test_detect_physical_local_address_skips_tun_and_fake_ip(self) -> None:
        http_client.detect_physical_local_address.cache_clear()

        with patch.object(
            http_client,
            "_iter_local_ipv4_addresses",
            return_value=[
                ("lo0", "127.0.0.1"),
                ("utun7", "198.18.0.5"),
                ("en1", "192.168.31.20"),
            ],
        ):
            self.assertEqual(http_client.detect_physical_local_address(), "192.168.31.20")

    def test_configured_local_address_overrides_auto_detection(self) -> None:
        http_client.detect_physical_local_address.cache_clear()

        with patch.dict("os.environ", {"AI8VIDEO_API_LOCAL_ADDRESS": "10.0.0.8"}):
            self.assertEqual(http_client.detect_physical_local_address(), "10.0.0.8")

    def test_is_direct_mapped_url_uses_explicit_host_map(self) -> None:
        with patch.dict(http_client._DIRECT_HOST_IPS, {"api.example.com": "203.0.113.10"}, clear=True):
            self.assertTrue(http_client.is_direct_mapped_url("https://api.example.com/v1/chat/completions"))
            self.assertFalse(http_client.is_direct_mapped_url("https://example.test/v1/chat/completions"))

    def test_fake_ip_url_does_not_bind_physical_interface(self) -> None:
        fake_ip_result = [(2, 1, 6, "", ("198.18.0.45", 0))]
        with patch.object(http_client, "detect_physical_local_address", return_value="192.168.31.20"), patch.object(
            http_client.socket,
            "getaddrinfo",
            return_value=fake_ip_result,
        ):
            self.assertIsNone(http_client._direct_local_address_for_url("https://api.example.com/v1/models"))

    def test_public_ip_url_keeps_physical_interface_binding(self) -> None:
        public_ip_result = [(2, 1, 6, "", ("104.18.32.47", 0))]
        with patch.object(http_client, "detect_physical_local_address", return_value="192.168.31.20"), patch.object(
            http_client.socket,
            "getaddrinfo",
            return_value=public_ip_result,
        ):
            self.assertEqual(
                http_client._direct_local_address_for_url("https://api.example.com/v1/models"),
                "192.168.31.20",
            )

    def test_parse_ifconfig_ipv4_addresses_keeps_interface_names(self) -> None:
        output = """
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
    inet 127.0.0.1 netmask 0xff000000
utun7: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1500
    inet 198.18.0.5 --> 198.18.0.5 netmask 0xffffffff
en1: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
    inet 192.168.31.20 netmask 0xffffff00 broadcast 192.168.31.255
"""

        self.assertEqual(
            http_client._parse_ifconfig_ipv4_addresses(output),
            [("lo0", "127.0.0.1"), ("utun7", "198.18.0.5"), ("en1", "192.168.31.20")],
        )


if __name__ == "__main__":
    unittest.main()
