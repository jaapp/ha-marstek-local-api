"""Marstek Local API UDP client."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import socket
import time
from copy import deepcopy
from typing import Any

from .const import (
    ALL_API_METHODS,
    COMMAND_BACKOFF_BASE,
    COMMAND_BACKOFF_FACTOR,
    COMMAND_BACKOFF_JITTER,
    COMMAND_BACKOFF_MAX,
    COMMAND_MAX_ATTEMPTS,
    COMMAND_TIMEOUT,
    DEFAULT_PORT,
    DISCOVERY_BROADCAST_INTERVAL,
    DISCOVERY_TIMEOUT,
    ERROR_METHOD_NOT_FOUND,
    METHOD_BATTERY_STATUS,
    METHOD_BLE_STATUS,
    METHOD_EM_STATUS,
    METHOD_ES_MODE,
    METHOD_ES_SET_MODE,
    METHOD_ES_STATUS,
    METHOD_GET_DEVICE,
    METHOD_PV_STATUS,
    METHOD_WIFI_STATUS,
)

_LOGGER = logging.getLogger(__name__)

# Shared transports and protocols per port to ensure all clients on the same port
# share the same UDP socket and can receive all messages
_shared_transports = {}
_shared_protocols = {}
_transport_refcounts = {}
_clients_by_port = {}  # Map port -> list of clients
_shared_command_locks = {}  # Map port -> asyncio.Lock for serializing command/response cycles


class MarstekUDPClient:
    """UDP client for Marstek Local API communication."""

    def __init__(self, hass, host: str | None = None, port: int = DEFAULT_PORT, remote_port: int | None = None) -> None:
        """Initialize the UDP client.

        Args:
            hass: Home Assistant instance
            host: Target host IP (None for broadcast)
            port: Local port to bind to (0 for ephemeral)
            remote_port: Remote port to send to (defaults to DEFAULT_PORT)
        """
        self.hass = hass
        self.host = host
        self.port = port
        self.remote_port = remote_port or DEFAULT_PORT
        self.transport: asyncio.DatagramTransport | None = None
        self.protocol: MarstekProtocol | None = None
        self._handlers: list = []
        self._connected = False
        self._stale_message_counter = 0
        self._command_stats: dict[str, dict[str, Any]] = {}
        self._msg_id_counter = 0  # Counter for integer message IDs

    async def connect(self) -> None:
        """Connect to the UDP socket."""
        if self._connected and self.transport:
            _LOGGER.debug("Already connected on port %s", self.port)
            return

        loop = asyncio.get_event_loop()
        self._loop = loop

        _LOGGER.info(
            "Connecting UDP socket: local_port=%s, remote_host=%s, remote_port=%s",
            self.port, self.host or "broadcast", self.remote_port
        )

        try:
            # Use shared transport/protocol for this port to ensure all clients
            # on the same port can receive all UDP messages
            if self.port not in _shared_transports:
                # Create shared UDP endpoint for this port
                import sys
                endpoint_kwargs = {
                    "local_addr": ("0.0.0.0", self.port),
                    "allow_broadcast": True,
                }
                # reuse_port is not supported on Windows
                if sys.platform != "win32":
                    endpoint_kwargs["reuse_port"] = True
                transport, protocol = await loop.create_datagram_endpoint(
                    lambda: MarstekProtocol(),
                    **endpoint_kwargs,
                )
                _shared_transports[self.port] = transport
                _shared_protocols[self.port] = protocol
                _transport_refcounts[self.port] = 0

                _LOGGER.info(
                    "Created shared UDP socket on port %s",
                    self.port
                )

            # Use the shared transport/protocol
            self.transport = _shared_transports[self.port]
            self.protocol = _shared_protocols[self.port]
            _transport_refcounts[self.port] += 1

            # Register this client for message dispatching
            if self.port not in _clients_by_port:
                _clients_by_port[self.port] = []
            if self not in _clients_by_port[self.port]:
                _clients_by_port[self.port].append(self)

            self._connected = True
            sock = self.transport.get_extra_info('socket')
            _LOGGER.info(
                "UDP socket connected: local_port=%s, socket=%s, refcount=%d, clients=%d",
                self.port, sock.getsockname() if sock else "unknown",
                _transport_refcounts[self.port], len(_clients_by_port[self.port])
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to connect UDP socket on port %s: %s",
                self.port, err, exc_info=True
            )
            raise

    async def disconnect(self) -> None:
        """Disconnect from the UDP socket."""
        if not self._connected:
            return

        if self.port in _transport_refcounts:
            # Unregister this client from message dispatching
            if self.port in _clients_by_port and self in _clients_by_port[self.port]:
                _clients_by_port[self.port].remove(self)

            _transport_refcounts[self.port] -= 1

            # Only close the shared transport when last client disconnects
            if _transport_refcounts[self.port] <= 0:
                if self.transport:
                    try:
                        self.transport.close()
                    except Exception as err:
                        _LOGGER.warning("Error closing transport: %s", err)

                if self.port in _shared_transports:
                    del _shared_transports[self.port]
                if self.port in _shared_protocols:
                    del _shared_protocols[self.port]
                if self.port in _transport_refcounts:
                    del _transport_refcounts[self.port]
                if self.port in _clients_by_port:
                    del _clients_by_port[self.port]
                if self.port in _shared_command_locks:
                    del _shared_command_locks[self.port]
                _LOGGER.debug("Closed shared UDP socket on port %s", self.port)
            else:
                _LOGGER.debug(
                    "UDP socket disconnected, %d clients still connected on port %s",
                    _transport_refcounts[self.port], self.port
                )

        self.transport = None
        self.protocol = None
        self._connected = False

    def register_handler(self, handler) -> None:
        """Register a message handler."""
        if handler not in self._handlers:
            self._handlers.append(handler)

    def unregister_handler(self, handler) -> None:
        """Unregister a message handler."""
        if handler in self._handlers:
            self._handlers.remove(handler)

    async def _handle_message(self, data: bytes, addr: tuple) -> None:
        """Handle incoming UDP message.

        This method is called by the shared protocol and needs to dispatch
        the message to all clients sharing this port.
        """
        try:
            message = json.loads(data.decode())
            _LOGGER.debug(
                "Received UDP message from %s:%s (size=%d bytes): %s",
                addr[0], addr[1], len(data), message
            )

            # Call all registered handlers from THIS client
            handlers_called = 0
            for handler in self._handlers:
                try:
                    # Handler can be sync or async
                    result = handler(message, addr)
                    if asyncio.iscoroutine(result):
                        await result
                    handlers_called += 1
                except Exception as err:
                    _LOGGER.error("Error in message handler: %s", err, exc_info=True)

            _LOGGER.debug("Called %d handler(s) for message from %s", handlers_called, addr[0])

        except json.JSONDecodeError as err:
            _LOGGER.error("Failed to decode JSON message from %s: %s (data: %s)", addr, err, data[:200])

    async def send_command(
        self,
        method: str,
        params: dict | None = None,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Send a command and wait for response."""
        if not self._connected:
            await self.connect()

        if self.port not in _shared_command_locks:
            _shared_command_locks[self.port] = asyncio.Lock()

        async with _shared_command_locks[self.port]:
            return await self._send_command_locked(method, params, timeout, max_attempts)

    async def _send_command_locked(
        self,
        method: str,
        params: dict | None = None,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Send a command while holding the shared port lock."""
        if not self._connected:
            await self.connect()

        if params is None:
            params = {"id": 0}

        effective_timeout = timeout if timeout is not None else COMMAND_TIMEOUT
        attempt_limit = max_attempts if max_attempts is not None else COMMAND_MAX_ATTEMPTS

        # Generate unique integer message ID (required for Venus E firmware V139+)
        self._msg_id_counter = (self._msg_id_counter + 1) % 1000000  # Wrap at 1 million
        msg_id = self._msg_id_counter
        payload = {
            "id": msg_id,
            "method": method,
            "params": params,
        }
        payload_str = json.dumps(payload)

        _LOGGER.debug(
            "Sending command: method=%s, id=%s, host=%s, port=%s, transport=%s",
            method, msg_id, self.host, self.remote_port, self.transport is not None
        )

        # Shared response tracking for all attempts
        response_event = asyncio.Event()
        response_data: dict[str, Any] = {}
        last_exception: Exception | None = None

        # Allow the event loop to process any pending datagrams before we start
        await asyncio.sleep(0)

        def handler(message, addr):
            """Handle command response."""
            if message.get("id") == msg_id:
                if self.host and addr[0] != self.host:
                    _LOGGER.debug("Ignoring response from wrong host: %s (expected %s)", addr[0], self.host)
                    return  # Wrong device
                _LOGGER.debug("Matched response for %s from %s", method, addr)
                response_data.clear()
                response_data.update(message)
                response_event.set()
            else:
                # Track stray messages so we know if queues are backing up
                self._stale_message_counter += 1
                if self._stale_message_counter <= 5 or self._stale_message_counter % 25 == 0:
                    _LOGGER.debug(
                        "Ignoring stale message while waiting for %s: got id=%s from %s (total stales=%d)",
                        method,
                        message.get("id"),
                        addr[0],
                        self._stale_message_counter,
                    )

        # Register temporary handler
        self.register_handler(handler)

        try:
            loop = asyncio.get_running_loop()

            for attempt in range(1, attempt_limit + 1):
                response_event.clear()
                response_data.clear()
                attempt_started = loop.time()

                try:
                    _LOGGER.debug(
                        "Sending payload (attempt %d/%d) to %s:%s: %s",
                        attempt,
                        attempt_limit,
                        self.host or "broadcast",
                        self.remote_port,
                        payload_str,
                    )
                    # Yield once more to ensure pending packets are processed before sending
                    await asyncio.sleep(0)
                    await self._send_to_host(payload_str)

                    await asyncio.wait_for(response_event.wait(), timeout=effective_timeout)

                    if "error" in response_data:
                        error = response_data["error"]
                        error_code = error.get('code')
                        error_msg = error.get('message')
                        # Record the error with its code for diagnostics
                        self._record_command_result(
                            method,
                            success=False,
                            attempt=attempt,
                            latency=None,
                            timeout=False,
                            error=error_msg,
                            error_code=error_code,
                        )
                        raise MarstekAPIError(
                            f"API error {error_code}: {error_msg}"
                        )

                    latency = loop.time() - attempt_started
                    self._stale_message_counter = 0
                    self._record_command_result(
                        method,
                        success=True,
                        attempt=attempt,
                        latency=latency,
                        timeout=False,
                        error=None,
                        error_code=None,
                        response=response_data,
                    )
                    _LOGGER.debug(
                        "Command %s completed successfully in %.2fs (attempt %d)",
                        method,
                        latency,
                        attempt,
                    )
                    return response_data.get("result")

                except asyncio.TimeoutError:
                    self._record_command_result(
                        method,
                        success=False,
                        attempt=attempt,
                        latency=None,
                        timeout=True,
                        error="timeout",
                    )
                    _LOGGER.warning(
                        "Command %s timed out after %ss (attempt %d/%d, host=%s)",
                        method,
                        effective_timeout,
                        attempt,
                        attempt_limit,
                        self.host,
                    )
                    last_exception = None
                except MarstekAPIError:
                    # Error already recorded in the if "error" block above
                    raise
                except Exception as err:
                    self._record_command_result(
                        method,
                        success=False,
                        attempt=attempt,
                        latency=None,
                        timeout=False,
                        error=str(err),
                    )
                    _LOGGER.error(
                        "Error sending command %s to %s on attempt %d/%d: %s",
                        method,
                        self.host,
                        attempt,
                        attempt_limit,
                        err,
                        exc_info=True,
                    )
                    last_exception = err

                if attempt < attempt_limit:
                    delay = self._compute_backoff_delay(attempt)
                    _LOGGER.debug(
                        "Waiting %.2fs before retrying %s (attempt %d/%d)",
                        delay,
                        method,
                        attempt + 1,
                        attempt_limit,
                    )
                    await asyncio.sleep(delay)

        finally:
            self.unregister_handler(handler)

        if last_exception:
            raise last_exception

        _LOGGER.error(
            "Command %s failed after %d attempt(s); returning no result",
            method,
            attempt_limit,
        )
        return None

    async def _send_to_host(self, message: str) -> None:
        """Send message to specific host or broadcast."""
        if not self.transport:
            raise MarstekAPIError("Not connected")

        if self.host:
            # Send to specific host on remote port
            self.transport.sendto(
                message.encode(),
                (self.host, self.remote_port)
            )
        else:
            # Broadcast
            await self.broadcast(message)

    def _compute_backoff_delay(self, attempt: int) -> float:
        """Compute exponential backoff with jitter for retries."""
        base_delay = COMMAND_BACKOFF_BASE * (COMMAND_BACKOFF_FACTOR ** (attempt - 1))
        capped = min(base_delay, COMMAND_BACKOFF_MAX)
        if COMMAND_BACKOFF_JITTER > 0:
            return capped + random.uniform(0, COMMAND_BACKOFF_JITTER)
        return capped

    def _record_command_result(
        self,
        method: str,
        *,
        success: bool,
        attempt: int,
        latency: float | None,
        timeout: bool,
        error: str | None,
        error_code: int | None = None,
        response: dict[str, Any] | None = None,
    ) -> None:
        """Track command attempt statistics for diagnostics."""
        stats = self._command_stats.setdefault(
            method,
            {
                "total_attempts": 0,
                "total_success": 0,
                "total_timeouts": 0,
                "total_failures": 0,
                "last_success": None,
                "last_attempt": None,
                "last_latency": None,
                "last_timeout": False,
                "last_error": None,
                "last_error_code": None,
                "last_updated": None,
                "last_success_at": None,
                "last_success_payload": None,
                "unsupported_error_count": 0,
                "supported": None,  # None=unknown, True=supported, False=unsupported
            },
        )

        stats["total_attempts"] += 1
        if success:
            stats["total_success"] += 1
            stats["supported"] = True  # Command works on this device
        elif timeout:
            stats["total_timeouts"] += 1
        else:
            stats["total_failures"] += 1

        stats["last_success"] = success
        stats["last_attempt"] = attempt
        stats["last_latency"] = latency
        stats["last_timeout"] = timeout
        stats["last_error"] = error
        stats["last_error_code"] = error_code
        stats["last_updated"] = time.time()
        if success:
            stats["last_success_at"] = stats["last_updated"]
            stats["last_success_payload"] = deepcopy(response) if response is not None else None

        # Track "Method not found" errors to detect unsupported commands
        if error_code == ERROR_METHOD_NOT_FOUND:
            stats["unsupported_error_count"] = stats.get("unsupported_error_count", 0) + 1
            # Mark as unsupported after 2+ method-not-found errors
            if stats["unsupported_error_count"] >= 2:
                stats["supported"] = False

    def get_command_stats(self, method: str) -> dict[str, Any] | None:
        """Return snapshot of command statistics."""
        stats = self._command_stats.get(method)
        if stats is None:
            return None
        return dict(stats)

    def get_all_command_stats(self) -> dict[str, dict[str, Any]]:
        """Return snapshot of all command statistics including never-attempted commands."""
        all_stats = {}
        for method in ALL_API_METHODS:
            if method in self._command_stats:
                all_stats[method] = dict(self._command_stats[method])
            else:
                # Never attempted - return default structure
                all_stats[method] = {
                    "total_attempts": 0,
                    "total_success": 0,
                    "total_timeouts": 0,
                    "total_failures": 0,
                    "last_success": None,
                    "last_attempt": None,
                    "last_latency": None,
                    "last_timeout": False,
                    "last_error": None,
                    "last_error_code": None,
                    "last_updated": None,
                    "last_success_at": None,
                    "last_success_payload": None,
                    "unsupported_error_count": 0,
                    "supported": None,
                }
        return all_stats

    async def broadcast(self, message: str) -> None:
        """Broadcast a message."""
        if not self.transport:
            await self.connect()

        # Get broadcast address
        broadcast_addr = self._get_broadcast_address()

        self.transport.sendto(
            message.encode(),
            (broadcast_addr, self.remote_port)
        )
        _LOGGER.debug("Broadcast message: %s", message)

    def _get_broadcast_addresses(self) -> list[str]:
        """Get all broadcast addresses for available networks.

        Uses simple heuristic: broadcast on /24 of primary interface and global broadcast.
        This works for most home networks and avoids VPN interfaces.
        """
        import struct
        import subprocess

        broadcast_addrs = set()

        try:
            # Parse ifconfig to get all network interfaces and their IPs
            result = subprocess.run(['ifconfig'], capture_output=True, text=True, timeout=2)
            current_ip = None

            for line in result.stdout.split('\n'):
                # Parse inet lines
                if '\tinet ' in line:
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[0] == 'inet':
                        ip = parts[1]

                        # Skip loopback
                        if ip.startswith('127.'):
                            continue

                        # Parse netmask if present
                        netmask = None
                        if 'netmask' in parts:
                            idx = parts.index('netmask')
                            if idx + 1 < len(parts):
                                mask_hex = parts[idx + 1]
                                # Skip point-to-point /32 (VPN) interfaces
                                if mask_hex == '0xffffffff':
                                    continue

                                # Convert hex netmask to dotted decimal
                                try:
                                    mask_int = int(mask_hex, 16)
                                    netmask = socket.inet_ntoa(struct.pack('>I', mask_int))
                                except (ValueError, OSError):
                                    pass

                        # Check for explicit broadcast address
                        if 'broadcast' in parts:
                            idx = parts.index('broadcast')
                            if idx + 1 < len(parts):
                                broadcast_addrs.add(parts[idx + 1])
                        elif netmask:
                            # Calculate broadcast address
                            try:
                                ip_int = struct.unpack('>I', socket.inet_aton(ip))[0]
                                mask_int = struct.unpack('>I', socket.inet_aton(netmask))[0]
                                broadcast_int = ip_int | (~mask_int & 0xffffffff)
                                broadcast = socket.inet_ntoa(struct.pack('>I', broadcast_int))
                                broadcast_addrs.add(broadcast)
                            except (ValueError, OSError):
                                pass
                        else:
                            # Assume /24 network
                            parts_ip = ip.split(".")
                            if len(parts_ip) == 4:
                                broadcast_addrs.add(f"{parts_ip[0]}.{parts_ip[1]}.{parts_ip[2]}.255")

        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as err:
            _LOGGER.debug("Could not parse ifconfig: %s, using fallback", err)

        # If we found nothing, use global broadcast as fallback
        if not broadcast_addrs:
            broadcast_addrs.add("255.255.255.255")

        return list(broadcast_addrs)

    def _get_broadcast_address(self) -> str:
        """Get primary broadcast address (for backward compatibility)."""
        addrs = self._get_broadcast_addresses()
        return addrs[0] if addrs else "255.255.255.255"

    async def discover_devices(self, timeout: int = DISCOVERY_TIMEOUT) -> list[dict]:
        """Discover Marstek devices on the network."""
        devices = []
        discovered_macs = set()

        def handler(message, addr):
            """Handle discovery responses."""
            msg_id = message.get("id")
            has_result = "result" in message
            _LOGGER.debug("Discovery handler called: id=%s, expected=0, match=%s, has_result=%s",
                         msg_id, msg_id == 0, has_result)

            if msg_id == 0 and has_result:
                result = message["result"]
                wifi_mac = result.get("wifi_mac")
                ble_mac = result.get("ble_mac")
                ip = addr[0]

                if not ble_mac:
                    _LOGGER.debug(
                        "Skipping discovery response without BLE MAC: wifi_mac=%s ip=%s",
                        wifi_mac,
                        ip,
                    )
                    return

                if ble_mac in discovered_macs:
                    _LOGGER.debug(
                        "Discovery response for %s already processed (ip=%s)",
                        ble_mac,
                        ip,
                    )
                    return

                discovered_macs.add(ble_mac)
                device = {
                    "name": result.get("device", "Unknown"),
                    "ip": ip,
                    "mac": ble_mac,
                    "firmware": result.get("ver", 0),
                    "ble_mac": ble_mac,
                    "wifi_mac": wifi_mac,
                    "wifi_name": result.get("wifi_name"),
                }
                devices.append(device)
                _LOGGER.info("Added discovered device: %s", device)

        # Register handler
        self.register_handler(handler)

        try:
            # Get all broadcast addresses
            broadcast_addrs = self._get_broadcast_addresses()
            _LOGGER.debug("Broadcasting to networks: %s", broadcast_addrs)

            # Broadcast discovery message repeatedly on all networks
            end_time = asyncio.get_event_loop().time() + timeout
            message = json.dumps({
                "id": 0,
                "method": METHOD_GET_DEVICE,
                "params": {"ble_mac": "0"}
            })

            while asyncio.get_event_loop().time() < end_time:
                # Broadcast to all networks
                for broadcast_addr in broadcast_addrs:
                    if self.transport:
                        self.transport.sendto(
                            message.encode(),
                            (broadcast_addr, self.remote_port)
                        )
                await asyncio.sleep(DISCOVERY_BROADCAST_INTERVAL)

            # Wait a bit longer for any delayed responses
            _LOGGER.debug("Waiting for delayed responses...")
            await asyncio.sleep(2)

        finally:
            self.unregister_handler(handler)
            _LOGGER.info("Discovery complete - found %d device(s)", len(devices))

        return devices

    # API method helpers
    async def get_device_info(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get device information."""
        return await self.send_command(
            METHOD_GET_DEVICE,
            {"ble_mac": "0"},
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_wifi_status(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get WiFi status."""
        return await self.send_command(
            METHOD_WIFI_STATUS,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_ble_status(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get Bluetooth status."""
        return await self.send_command(
            METHOD_BLE_STATUS,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_battery_status(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get battery status."""
        return await self.send_command(
            METHOD_BATTERY_STATUS,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_pv_status(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get PV (solar) status."""
        return await self.send_command(
            METHOD_PV_STATUS,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_es_status(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get energy system status."""
        return await self.send_command(
            METHOD_ES_STATUS,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_es_mode(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get energy system operating mode."""
        return await self.send_command(
            METHOD_ES_MODE,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_em_status(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get energy meter (CT) status."""
        return await self.send_command(
            METHOD_EM_STATUS,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def set_es_mode(self, config: dict) -> bool:
        """Set energy system operating mode."""
        result = await self.send_command(
            METHOD_ES_SET_MODE,
            {"id": 0, "config": config}
        )

        if result and result.get("set_result"):
            return True
        return False


class MarstekProtocol(asyncio.DatagramProtocol):
    """Protocol for handling UDP datagrams.

    This protocol is shared across all clients on the same port.
    It dispatches incoming messages to all registered clients.
    """

    def __init__(self) -> None:
        """Initialize the protocol."""
        self.port = None  # Will be set when socket is bound

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        """Handle received datagram.

        Dispatch to all clients registered on this port.
        """
        # Get the local port from the transport
        if self.port is None:
            try:
                sock = None
                # Try to get port from connection (we'll set it properly below)
                for port, protocol in _shared_protocols.items():
                    if protocol is self:
                        self.port = port
                        break
            except Exception:
                pass

        # Dispatch to all clients on this port
        if self.port and self.port in _clients_by_port:
            for client in _clients_by_port[self.port]:
                asyncio.create_task(client._handle_message(data, addr))
        else:
            _LOGGER.warning("Received message but no clients registered for port %s", self.port)

    def error_received(self, exc: Exception) -> None:
        """Handle protocol errors."""
        _LOGGER.error("Protocol error: %s", exc)


class MarstekAPIError(Exception):
    """Exception for Marstek API errors."""
