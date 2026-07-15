# Marstek Local API Integration - Design Document

## Overview

Home Assistant integration for Marstek energy storage systems using the official Local API (Rev 1.0). Provides comprehensive monitoring and control of Marstek Venus A/C/D/E devices without requiring cloud connectivity or additional hardware.

**Version:** 1.0
**Target Devices:** Venus A, Venus C, Venus D, Venus E
**Protocol:** JSON over UDP (port 30000+)
**Requirements:** Local API enabled in Marstek app

---

## Architecture

### Component Structure

```
marstek_local_api/
├── __init__.py              # Integration setup, coordinator
├── manifest.json            # Integration metadata
├── config_flow.py           # UI configuration flow
├── const.py                 # Constants and mappings
├── coordinator.py           # Data update coordinator
├── api.py                   # Local API client
├── sensor.py               # Sensor platform
├── binary_sensor.py        # Binary sensor platform
├── switch.py               # Switch platform (future)
└── select.py               # Select platform (operating modes)
```

### Data Flow

```
┌─────────────────────────────────────────────────┐
│  Home Assistant                                 │
│                                                 │
│  ┌─────────────────────────────────────────┐   │
│  │  MarstekDataUpdateCoordinator           │   │
│  │  (polls every 30s)                      │   │
│  │                                         │   │
│  │  ┌─────────────────────────────────┐   │   │
│  │  │  MarstekLocalAPI                │   │   │
│  │  │  - Device discovery (UDP)       │   │   │
│  │  │  - Multiple method calls        │   │   │
│  │  │  - Error handling               │   │   │
│  │  └─────────────────────────────────┘   │   │
│  └─────────────────────────────────────────┘   │
│                    │                            │
│                    ▼                            │
│  ┌─────────────────────────────────────────┐   │
│  │  Entities (Sensors, Binary Sensors)     │   │
│  │  - Battery sensors                      │   │
│  │  - Grid/CT sensors                      │   │
│  │  - Energy system sensors                │   │
│  │  - PV sensors (Venus D)                 │   │
│  │  - Network sensors                      │   │
│  │  - Calculated sensors                   │   │
│  └─────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
                     │
                     │ JSON/UDP (port 30000+)
                     ▼
          ┌──────────────────────┐
          │  Marstek Venus E     │
          │  (192.168.x.x:30000) │
          └──────────────────────┘
```

---

## Supported API Components

### 3.1 Marstek (Device Discovery)
**Method:** `Marstek.GetDevice`

| Field | Type | Sensor | Description |
|-------|------|--------|-------------|
| device | string | ✅ Text | Device model (VenusC, VenusD, VenusE) |
| ver | number | ✅ Sensor | Firmware version |
| ble_mac | string | ✅ Text | Bluetooth MAC |
| wifi_mac | string | ✅ Text | WiFi MAC |
| wifi_name | string | ✅ Text | WiFi SSID |
| ip | string | ✅ Text | Device IP address |

### 3.2 WiFi
**Method:** `Wifi.GetStatus`

| Field | Type | Sensor | Description |
|-------|------|--------|-------------|
| ssid | string | ✅ Text | WiFi name |
| rssi | number | ✅ Sensor | WiFi signal strength (dBm) |
| sta_ip | string | ✅ Text | Device IP |
| sta_gate | string | ✅ Text | Gateway IP |
| sta_mask | string | ✅ Text | Subnet mask |
| sta_dns | string | ✅ Text | DNS server |

### 3.3 Bluetooth
**Method:** `BLE.GetStatus`

| Field | Type | Sensor | Description |
|-------|------|--------|-------------|
| state | string | ✅ Binary | Bluetooth connection state |
| ble_mac | string | ✅ Text | Bluetooth MAC address |

### 3.4 Battery
**Method:** `Bat.GetStatus`

| Field | Type | Sensor | Description |
|-------|------|--------|-------------|
| soc | number | ✅ Sensor | State of Charge (%) |
| charg_flag | boolean | ✅ Binary | Charging permission flag |
| dischrg_flag | boolean | ✅ Binary | Discharge permission flag |
| bat_temp | number | ✅ Sensor | Battery temperature (°C) |
| bat_capacity | number | ✅ Sensor | Battery remaining capacity (Wh) |
| rated_capacity | number | ✅ Sensor | Battery rated capacity (Wh) |

**Calculated Sensors:**
- Available Capacity: `(100 - SOC) × rated_capacity / 100` (Wh)
- Battery State: `charging` / `discharging` / `idle` (based on bat_power)

### 3.5 PV (Photovoltaic) - Venus D and Venus A
**Method:** `PV.GetStatus`

Venus D response (single MPPT summary):

| Field | Type | Sensor | Description |
|-------|------|--------|-------------|
| pv_power | number | ✅ Sensor | Solar charging power (W) |
| pv_voltage | number | ✅ Sensor | Solar voltage (V) |
| pv_current | number | ✅ Sensor | Solar current (A) |

Venus A response (per string, observed on fw 148):

| Field | Type | Sensor | Description |
|-------|------|--------|-------------|
| pv1_power … pv4_power | number | ✅ Sensor | String power — **unit differs per string** (fw 148: pv1 deci-W, pv2 plain W). The coordinator disambiguates each string against V×I (`_scale_pv_string_power`), falling back to the matrix ÷10 when V×I is too small to discriminate |
| pv1_voltage … pv4_voltage | number | ✅ Sensor | String voltage (V) |
| pv1_current … pv4_current | number | ❌ | String current, plain A truncated to integer (1 A resolution) — too coarse to expose |
| pv1_state … pv4_state | number | ❌ | 1 = string active, 0 = inactive |

On Venus A the coordinator polls PV on the fast tier (every update): the string
sum replaces the always-zero `es.pv_power`, and battery power is derived as
`pv_power - ongrid_power - offgrid_power` because `ES.GetStatus` omits
`bat_power` on this model.

### 3.6 ES (Energy System)
**Method:** `ES.GetStatus`

| Field | Type | Sensor | Description |
|-------|------|--------|-------------|
| bat_soc | number | ✅ Sensor | Total battery SOC (%) |
| bat_cap | number | ✅ Sensor | Total battery capacity (Wh) |
| bat_power | number | ✅ Sensor | Battery power (W, +charge/-discharge) |
| pv_power | number | ✅ Sensor | Solar charging power (W) |
| ongrid_power | number | ✅ Sensor | Grid-tied power (W) |
| offgrid_power | number | ✅ Sensor | Off-grid power (W) |
| total_pv_energy | number | ✅ Sensor | Total solar energy generated (Wh) |
| total_grid_output_energy | number | ✅ Sensor | Total grid export energy (Wh) |
| total_grid_input_energy | number | ✅ Sensor | Total grid import energy (Wh) |
| total_load_energy | number | ✅ Sensor | Total load energy consumed (Wh) |

**Method:** `ES.GetMode`

| Field | Type | Sensor | Description |
|-------|------|--------|-------------|
| mode | string | ✅ Select | Operating mode (Auto/AI/Manual/Passive) |
| ongrid_power | number | ✅ Sensor | Current grid power (W) |
| offgrid_power | number | ✅ Sensor | Current off-grid power (W) |
| bat_soc | number | ✅ Sensor | Battery SOC (%) |

**Method:** `ES.SetMode` (Control)
- Set operating mode: Auto, AI, Manual, Passive
- Configure time schedules (Manual mode)
- Set power limits (Passive mode)

### 3.7 EM (Energy Meter / CT)
**Method:** `EM.GetStatus`

| Field | Type | Sensor | Description |
|-------|------|--------|-------------|
| ct_state | number | ✅ Binary | CT connection status (0=disconnected, 1=connected) |
| a_power | number | ✅ Sensor | Phase A power (W) |
| b_power | number | ✅ Sensor | Phase B power (W) |
| c_power | number | ✅ Sensor | Phase C power (W) |
| total_power | number | ✅ Sensor | Total power (W) |

---

## Calculated/Derived Sensors

### Battery Power Flow
Based on `ES.GetStatus.bat_power`:

| Sensor | Calculation | Purpose |
|--------|-------------|---------|
| Battery Power In | `max(0, bat_power)` | Positive charging power |
| Battery Power Out | `max(0, -bat_power)` | Positive discharging power |
| Battery State | Based on bat_power sign | Text: charging/discharging/idle |

### Energy Dashboard Integration
Using Home Assistant's integration platform:

| Sensor | Source | Integration Type |
|--------|--------|------------------|
| Battery Energy In | Battery Power In | Integration (Riemann sum) |
| Battery Energy Out | Battery Power Out | Integration (Riemann sum) |
| Daily Battery In | Battery Energy In | Utility meter (daily cycle) |
| Daily Battery Out | Battery Energy Out | Utility meter (daily cycle) |

### System Aggregates
For multi-device setups:

| Sensor | Calculation | Description |
|--------|-------------|-------------|
| System Total Battery Power | Sum all `bat_power` | Total system battery power |
| System Average SOC | Average all `bat_soc` | Average system SOC |
| System Total Capacity | Sum all `bat_cap` | Total system capacity |

---

## Device Structure

### Single Device Setup
```
Venus E (192.168.1.10)
├── Battery
│   ├── SOC (%)
│   ├── Temperature (°C)
│   ├── Capacity (Wh)
│   ├── Rated Capacity (Wh)
│   ├── Available Capacity (Wh) [calc]
│   ├── Power (W)
│   ├── Power In (W) [calc]
│   ├── Power Out (W) [calc]
│   ├── Charging Flag (binary)
│   ├── Discharging Flag (binary)
│   └── State (text) [calc]
│
├── Energy System
│   ├── Operating Mode (select)
│   ├── Grid Power (W)
│   ├── Off-Grid Power (W)
│   ├── Total PV Energy (Wh)
│   ├── Total Grid Import (Wh)
│   ├── Total Grid Export (Wh)
│   └── Total Load Energy (Wh)
│
├── Grid/CT Meter
│   ├── CT Connected (binary)
│   ├── Phase A Power (W)
│   ├── Phase B Power (W)
│   ├── Phase C Power (W)
│   └── Total Power (W)
│
├── Solar (Venus D only)
│   ├── PV Power (W)
│   ├── PV Voltage (V)
│   └── PV Current (A)
│
├── Network
│   ├── WiFi SSID (text)
│   ├── WiFi RSSI (dBm)
│   ├── WiFi IP (text)
│   ├── WiFi Gateway (text)
│   ├── WiFi Subnet (text)
│   ├── WiFi DNS (text)
│   ├── BLE Connected (binary)
│   └── BLE MAC (text)
│
└── Device Info
    ├── Model (text)
    ├── Firmware Version (sensor)
    ├── WiFi MAC (text)
    └── IP Address (text)
```

### Multi-Device Setup
```
Marstek System
├── Venus E #1 (Individual sensors as above)
├── Venus E #2 (Individual sensors as above)
└── System Aggregates
    ├── Total Battery Power (W)
    ├── Average SOC (%)
    ├── Total Capacity (Wh)
    └── Combined Grid Power (W)
```

---

## Configuration Flow

### Step 1: Discovery
- UDP broadcast `Marstek.GetDevice` on port 30000
- Auto-discover all Marstek devices on LAN
- Display discovered devices with model and IP

### Step 2: Device Selection
- User selects device(s) to add
- Option: "Add all discovered devices"
- Option: "Manual IP entry" (if discovery fails)

### Step 3: Configuration
```yaml
Configuration Fields:
- Device IP: 192.168.1.10
- Port: 30000 (default, customizable)
- Update Interval: 60s (default, base interval for tiered polling)
- Device Name: (optional, auto-fills with model)
```

### Step 4: Options Flow
After setup, user can configure:
- Update interval
- Enable/disable specific sensor categories
- Multi-device aggregation settings

---

## API Client Implementation

### Core Methods
```python
class MarstekLocalAPI:
    async def discover_devices(self) -> list[dict]
    async def get_device_info(self, ip: str, port: int) -> dict
    async def get_wifi_status(self, ip: str, port: int) -> dict
    async def get_ble_status(self, ip: str, port: int) -> dict
    async def get_battery_status(self, ip: str, port: int) -> dict
    async def get_pv_status(self, ip: str, port: int) -> dict
    async def get_es_status(self, ip: str, port: int) -> dict
    async def get_es_mode(self, ip: str, port: int) -> dict
    async def set_es_mode(self, ip: str, port: int, config: dict) -> bool
    async def get_em_status(self, ip: str, port: int) -> dict
```

### Error Handling
- **Timeout**: Retry once, then mark device unavailable
- **JSON Parse Error**: Log and skip update cycle
- **Method Not Found**: Device doesn't support this feature
- **Invalid Params**: Log configuration issue

---

## Data Update Strategy

### Coordinator Pattern
```python
class MarstekDataUpdateCoordinator(DataUpdateCoordinator):
    update_interval = 60 seconds (base interval)

    async def _async_update_data():
        # See "Polling Strategy" section for tiered polling implementation
        # High priority (60s): ES, Battery
        # Medium priority (300s): EM, PV, Mode
        # Low priority (600s): Device, WiFi, BLE
        return data
```

### Update Intervals
- **Fast poll** (60s): ES & Battery status — real-time power/energy
- **Medium poll** (300s): EM, PV, Mode — slower-changing data
- **Slow poll** (600s): Device, WiFi, BLE — static/diagnostic data

---

## Energy Dashboard Integration

### Configuration
```yaml
# Automatic sensor configuration for Energy Dashboard

Grid Consumption:
  - sensor.marstek_total_grid_input_energy

Grid Return:
  - sensor.marstek_total_grid_output_energy

Solar Production:
  - sensor.marstek_total_pv_energy

Battery:
  - Energy going in: sensor.marstek_battery_energy_in
  - Energy going out: sensor.marstek_battery_energy_out
```

### Required Sensor Attributes
- `device_class`: energy
- `state_class`: total_increasing
- `unit_of_measurement`: Wh or kWh

---

## Control Features

### Operating Mode Control
**Entity:** `select.marstek_operating_mode`

Options:
- Auto: Automatic mode
- AI: AI-based optimization
- Manual: Time-based schedules
- Passive: Fixed power control

### Future Controls (via ES.SetMode)
- Manual mode schedules
- Power limit settings
- Passive mode power/countdown

---

## Comparison: Local API vs BLE Gateway

| Feature | Local API | BLE Gateway | Winner |
|---------|-----------|-------------|--------|
| **Setup** | Enable in app | ESP32 hardware | Local API |
| **Reliability** | Network-based | BLE range limited | Local API |
| **Official Support** | ✅ Official | ❌ Reverse-eng | Local API |
| **Battery Data** | SOC, temp, capacity, power | + individual cells, V, I | BLE |
| **Grid/CT Data** | ✅ Full phase data | ❌ None | Local API |
| **Solar Data** | ✅ PV stats | ❌ None | Local API |
| **Operating Mode** | ✅ Read + Control | ❌ None | Local API |
| **Energy Totals** | ✅ Grid, PV, load | ❌ None | Local API |
| **Output Control** | ❌ Not supported | ✅ Full control | BLE |
| **Cell Voltages** | ❌ Not available | ✅ 16 cells | BLE |

**Recommendation:** Use Local API for comprehensive system monitoring. Use BLE Gateway only if you need individual cell voltages or output control.

---

## Device Compatibility

| Device | Marstek | WiFi | BLE | Battery | PV | ES | EM |
|--------|---------|------|-----|---------|----|----|-----|
| Venus C | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ |
| Venus E | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ |
| Venus D | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Venus A | ✅ | ✅ | ✅ | ✅ (no voltage/current/error_code) | ✅ (per string) | ✅ (no bat_power; pv/load energy always 0) | ✅ (no parse_state) |

Venus A reports plain units (W / Wh / °C) for every field except per-string PV
power, whose unit differs per string and is disambiguated against V×I. See the
`"A"` hardware profile in `compatibility.py` and `_scale_pv_string_power`.

---

## Firmware Version Handling

### Version Detection
On device setup, query firmware version from `Marstek.GetDevice` response (`ver` field).

### Value Scaling by Firmware

Different firmware versions use different decimal multipliers for certain values:

| Field | Firmware < 154 | Firmware >= 154 |
|-------|----------------|-----------------|
| `bat_temp` | ÷ 10.0 | ÷ 1.0 |
| `bat_capacity` | ÷ 100.0 | ÷ 1000.0 |
| `bat_power` | ÷ 10.0 | ÷ 1.0 |
| `total_grid_input_energy` | ÷ 100.0 | ÷ 10.0 |
| `total_grid_output_energy` | ÷ 100.0 | ÷ 10.0 |
| `total_load_energy` | ÷ 100.0 | ÷ 10.0 |

**Implementation:**
- Store firmware version in device data
- Apply appropriate multiplier in sensor value_template or during data parsing
- Log firmware version on integration setup for diagnostics

---

## Polling Strategy

### Non-Uniform Update Intervals

Not all API methods need equal polling frequency. Optimize network usage with tiered polling:

| Priority | Interval | Methods | Rationale |
|----------|----------|---------|-----------|
| High | 60s | `ES.GetStatus`, `Bat.GetStatus` | Real-time power/energy and battery state of charge |
| Medium | 300s | `EM.GetStatus`, `PV.GetStatus`, `ES.GetMode` | Slower-changing CT, solar, mode data |
| Low | 600s | `Marstek.GetDevice`, `Wifi.GetStatus`, `BLE.GetStatus` | Static/diagnostic data |

**Implementation Pattern:**
```python
class MarstekDataUpdateCoordinator:
    def __init__(self):
        self.update_count = 0
        self.base_interval = 60  # seconds

    async def _async_update_data(self):
        data = {}

        # Every update (60s)
        data["es"] = await self.api.get_es_status()
        data["battery"] = await self.api.get_battery_status()

        # Every 5th update (300s)
        if self.update_count % 5 == 0:
            data["pv"] = await self.api.get_pv_status()
            data["mode"] = await self.api.get_es_mode()
            data["em"] = await self.api.get_em_status()

        # Every 10th update (600s)
        if self.update_count % 10 == 0:
            data["device"] = await self.api.get_device_info()
            data["wifi"] = await self.api.get_wifi_status()
            data["ble"] = await self.api.get_ble_status()

        self.update_count += 1
        return data
```

---

## Reliability & Error Handling

### UDP Communication Challenges

Based on production use, Marstek UDP API has reliability issues:
- **Silent packet loss**: Battery ignores some requests without error
- **Communication stops**: UDP may stop working after extended operation
- **CT integration conflicts**: May conflict with CT002/CT003 devices

### Retry Mechanism

**Mode Change Commands:**
- Retry up to 5 times with 2-second delays
- Validate `set_result` field in response
- Timeout after 15 seconds per attempt

```python
async def set_mode(config: dict, retries: int = 5):
    for attempt in range(retries):
        try:
            result = await send_command("ES.SetMode", {"id": 0, "config": config}, timeout=15)
            if result.get("set_result") == True:
                return True
            raise ValueError("Device rejected mode change")
        except Exception as e:
            if attempt < retries - 1:
                await asyncio.sleep(2)
                continue
            raise

## Diagnostics & Telemetry

- The UDP client records per-method statistics (`total_attempts`, `total_success`, `total_timeouts`, `last_latency`).
- Device coordinators summarise these values together with poll timing (`target_interval`, `actual_interval`).
- A diagnostics handler (`diagnostics.py`) exposes the snapshot so users can download poll/latency numbers from Home Assistant's diagnostics panel.


### Command Response Matching

**Pattern:**
- Generate unique message ID: `f"homeassistant-{uuid4().hex[:8]}"`
- Register temporary handler for response
- Match response by ID field
- Timeout and cleanup after 15 seconds

```python
async def send_command(method: str, params: dict, timeout: int = 15):
    msg_id = f"homeassistant-{uuid4().hex[:8]}"
    payload = {"id": msg_id, "method": method, "params": params}

    response_event = asyncio.Event()
    response_data = {}

    def handler(json_msg):
        if json_msg.get("id") == msg_id:
            response_data.update(json_msg)
            response_event.set()

    self.register_handler(handler)
    try:
        await self.socket.send(json.dumps(payload))
        await asyncio.wait_for(response_event.wait(), timeout=timeout)
        return response_data.get("result")
    finally:
        self.unregister_handler(handler)
```

### Connection Health Monitoring

- Coordinators store `last_message_seconds` so diagnostics can report how long it has been since a successful response.
- Callers should mark the device unavailable when this exceeds `UNAVAILABLE_THRESHOLD` (120 seconds).

### Socket Architecture

**Single Shared Socket:**
- One UDP socket per HA instance (not per device)
- Multiple handler callbacks registered
- Handlers filter messages by source IP or message ID
- Prevents port binding conflicts

```python
class MarstekUDPSocket:
    def __init__(self):
        self.socket = None
        self.handlers = []

    def register_handler(self, callback):
        if callback not in self.handlers:
            self.handlers.append(callback)

    def unregister_handler(self, callback):
        if callback in self.handlers:
            self.handlers.remove(callback)

    async def on_message(self, data, addr):
        json_msg = json.loads(data.decode())
        for handler in self.handlers:
            await handler(json_msg, addr)
```

---

## Device Discovery

### Broadcast Discovery Protocol

**Timing:**
- Broadcast interval: 2 seconds
- Discovery window: 9 seconds total
- Message: `{"id":"homeassistant-discover","method":"Marstek.GetDevice","params":{"ble_mac":"0"}}`

**Implementation:**
```python
async def discover_devices(timeout: int = 9):
    devices = []
    discovered_macs = set()

    async def handler(json_msg, remote):
        if json_msg.get("method") == "Marstek.GetDevice" and "result" in json_msg:
            mac = json_msg["result"].get("wifi_mac")
            if mac and mac not in discovered_macs:
                discovered_macs.add(mac)
                devices.append({
                    "name": json_msg["result"]["device"],
                    "ip": remote[0],
                    "mac": mac,
                    "firmware": json_msg["result"]["ver"]
                })

    register_handler(handler)
    try:
        end_time = time.time() + timeout
        while time.time() < end_time:
            await broadcast(discover_message)
            await asyncio.sleep(2)
    finally:
        unregister_handler(handler)

    return devices
```

---

## Known Issues & Limitations

### UDP Reliability
- **Symptom**: Communication stops without error after hours/days
- **Mitigation**: Connection health monitoring, automatic reconnection
- **User Action**: May need to restart HA integration or Marstek device

### Packet Loss
- **Symptom**: Some UDP requests ignored by battery
- **Mitigation**: Retry mechanism for critical commands
- **Impact**: Occasional delayed updates

### Device Conflicts
- **Symptom**: Battery stops responding when CT002/CT003 BLE devices are paired
- **Cause**: Unknown (possibly firmware limitation)
- **Workaround**: Use Local API for CT data via EM.GetStatus instead of BLE CT devices

### Single Session Limit
- **Cloud API**: Marstek cloud only allows one active session per account
- **Local API**: Not affected - multiple integrations can access local API simultaneously

### Missing Features vs BLE Gateway
- No individual cell voltage monitoring (16 cells)
- No per-cell temperature sensors
- No direct output control switches
- No EPS mode control

---

## Diagnostic Sensors

Additional sensors for monitoring integration health:

| Sensor | Purpose | Update Interval |
|--------|---------|-----------------|
| Last Message Received | Seconds since last API response | 1s |
| Firmware Version | Device firmware version | On setup |
| WiFi RSSI | Signal strength diagnostic | 60s |
| BLE Connection State | Bluetooth status | 60s |
| CT Connection State | CT meter connectivity | 15s |

---

## Installation

### HACS Installation
1. Add custom repository: `https://github.com/[user]/marstek-local-api`
2. Install "Marstek Local API" integration
3. Restart Home Assistant
4. Enable Local API in Marstek app
5. Add integration via UI

### Requirements
- Home Assistant 2024.1.0 or newer
- Marstek device with Local API enabled
- Network connectivity to device

---

## Future Enhancements

### Phase 2
- [ ] Switch entities for device control
- [ ] Number entities for power limits
- [ ] Diagnostic sensors (error logs, events)
- [ ] Service calls for advanced control

### Phase 3
- [ ] Automation triggers on mode changes
- [ ] Battery health tracking over time
- [ ] Load balancing across multiple devices
- [ ] Integration with dynamic pricing

---

## Testing Strategy

### Unit Tests
- API client methods
- Data parsing
- Error handling
- Coordinator updates

### Integration Tests
- Config flow
- Sensor creation
- State updates
- Multi-device handling

### Manual Testing
- Discovery on real network
- All API methods
- Error scenarios
- Energy dashboard integration

---

## References

- [Marstek Device Open API Rev 1.0](../Marstek_Device_Open_API_EN_.Rev1.0.pdf)
- [Home Assistant Integration Development](https://developers.home-assistant.io/)
- [ESPHome BLE Gateway](../marstek-ble-gateway/) (for feature comparison)
- [Existing integrations](../home-assistant-marstek-local-api/) (for lessons learned)
