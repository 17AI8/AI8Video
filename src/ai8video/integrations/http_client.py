from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
from functools import lru_cache
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3 import PoolManager
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool


_TRUE_VALUES = {"1", "true", "yes", "on"}
_DIRECT_HOST_IPS: dict[str, str] = {}
_VIRTUAL_INTERFACE_PREFIXES = (
    "lo",
    "utun",
    "tun",
    "tap",
    "bridge",
    "docker",
    "veth",
    "gif",
    "stf",
    "awdl",
    "llw",
)


def use_system_proxy() -> bool:
    raw = str(os.getenv("AI8VIDEO_API_USE_SYSTEM_PROXY") or "").strip().lower()
    return raw in _TRUE_VALUES


def api_request(method: str, url: str, **kwargs) -> requests.Response:
    session = requests.Session()
    system_proxy_enabled = use_system_proxy()
    if not system_proxy_enabled:
        session.trust_env = False
        session.proxies.clear()
    local_address = None if system_proxy_enabled else _direct_local_address_for_url(url)
    if local_address:
        adapter = DirectNetworkAdapter(local_address=local_address)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    with session:
        return session.request(method, url, **kwargs)


def _direct_local_address_for_url(url: str) -> str | None:
    local_address = detect_physical_local_address()
    if not local_address or _url_resolves_to_fake_ip(url):
        return None
    return local_address


def _url_resolves_to_fake_ip(url: str) -> bool:
    hostname = str(urlsplit(url).hostname or "").strip()
    if not hostname:
        return False
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return any(_is_fake_ip(item[4][0]) for item in addresses)


def _is_fake_ip(address: str) -> bool:
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network("198.18.0.0/15")
    except ValueError:
        return False


class DirectNetworkAdapter(HTTPAdapter):
    def __init__(self, *, local_address: str) -> None:
        self.local_address = local_address
        super().__init__()

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs) -> None:
        pool_kwargs["source_address"] = (self.local_address, 0)
        self.poolmanager = DirectPoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )


class DirectPoolManager(PoolManager):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pool_classes_by_scheme = {
            "http": HTTPConnectionPool,
            "https": DirectHTTPSConnectionPool,
        }


class DirectHTTPSConnection(HTTPSConnection):
    def _new_conn(self):
        original_dns_host = getattr(self, "_dns_host", None)
        direct_address = _DIRECT_HOST_IPS.get(str(self.host or "").lower())
        if direct_address and original_dns_host is not None:
            self._dns_host = direct_address
        try:
            return super()._new_conn()
        finally:
            if direct_address and original_dns_host is not None:
                self._dns_host = original_dns_host


class DirectHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = DirectHTTPSConnection


@lru_cache(maxsize=1)
def detect_physical_local_address() -> str | None:
    configured_address = str(os.getenv("AI8VIDEO_API_LOCAL_ADDRESS") or "").strip()
    if configured_address:
        return configured_address
    for interface_name, address in _iter_local_ipv4_addresses():
        if _is_physical_interface_address(interface_name, address):
            return address
    return None


def _iter_local_ipv4_addresses() -> list[tuple[str, str]]:
    output = _run_network_command(["ifconfig"])
    if output:
        return _parse_ifconfig_ipv4_addresses(output)
    output = _run_network_command(["ip", "-o", "-4", "addr", "show"])
    if output:
        return _parse_ip_addr_ipv4_addresses(output)
    return []


def _run_network_command(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout


def _parse_ifconfig_ipv4_addresses(output: str) -> list[tuple[str, str]]:
    addresses: list[tuple[str, str]] = []
    current_interface = ""
    for line in output.splitlines():
        header_match = re.match(r"^([^:\s]+):", line)
        if header_match:
            current_interface = header_match.group(1)
            continue
        address_match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)\b", line)
        if current_interface and address_match:
            addresses.append((current_interface, address_match.group(1)))
    return addresses


def _parse_ip_addr_ipv4_addresses(output: str) -> list[tuple[str, str]]:
    addresses: list[tuple[str, str]] = []
    for line in output.splitlines():
        match = re.search(r"^\d+:\s+([^\s]+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/", line)
        if match:
            addresses.append((match.group(1), match.group(2)))
    return addresses


def _is_physical_interface_address(interface_name: str, address: str) -> bool:
    normalized_interface_name = interface_name.split("@")[0].lower()
    if normalized_interface_name.startswith(_VIRTUAL_INTERFACE_PREFIXES):
        return False
    try:
        ip_address = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip_address.version != 4:
        return False
    if ip_address.is_loopback or ip_address.is_link_local or ip_address.is_multicast:
        return False
    if ip_address in ipaddress.ip_network("198.18.0.0/15"):
        return False
    return True


def is_direct_mapped_url(url: str) -> bool:
    hostname = str(urlsplit(url).hostname or "").lower()
    return hostname in _DIRECT_HOST_IPS
