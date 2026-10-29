# teplo

Local-first controller for electric floor heating and household load balancing.
It runs 24/7 on a home router and needs no cloud at runtime.

> Status: design phase. The design lives in [`docs/design.md`](docs/design.md); code lands stage by stage (see [Roadmap](#roadmap)).

## Why

The house is heated by electric floors and fed by a 3-phase 15 kW connection with a 25 A breaker on the pole.
Two problems motivated teplo:

- **Cold mornings.** Floors were switched off for the night, and a one-hour morning boost cannot warm a room through a concrete screed.
- **Breaker trips.** An EV charges on a single phase behind an inverter voltage stabilizer. When a water heater, a floor zone and a microwave land on the same phase, the input breaker trips and the whole house goes dark.

teplo pre-heats rooms so they are warm by wake-up time, and keeps every phase under the breaker limit by shedding low-priority loads within seconds.

## Features

- **Morning comfort.** Per-zone weekly schedules. Optimal start learns how long each room needs to warm up from the indoor and outdoor temperatures.
- **Air-temperature regulation.** Floors are switched by room air temperature; the thermostat's floor setpoint stays a safety limit.
- **Per-phase load management.** Live current per phase from CT clamps, configurable thresholds, a configurable shedding priority and staggered starts.
- **Uncontrolled loads are visible.** EV charging and the washing machine are detected and budgeted for, never switched.
- **Tariff-aware.** Cheap, normal and expensive intervals are configuration, not code.
- **Offline by design.** Devices are driven over the LAN and the panel is served from the router. The internet is used only for Telegram alerts and NTP.
- **Fail-safe.** While the controller is down, thermostats hold a floor minimum, so the house never freezes.
- **Reusable.** The core is generic. A house is described by a site app with data migrations; secrets stay outside the repository.

## Hardware

| Role | Device | Integration | Milestone |
|---|---|---|---|
| Floor thermostats | Tuya Wi-Fi thermostats (tested: Warmfloor ET-44W) | Tuya local protocol | v1 |
| Smart plugs | Tuya Wi-Fi plugs with power metering | Tuya local protocol | v1 |
| Air and outdoor sensors | ESP32-C3 with SHT3x/SHT4x, DS18B20 | ESPHome over MQTT | v1 |
| Phase meter | ESP32 with 3× SCT-013 split-core CT clamps | ESPHome over MQTT | v1 |
| Heat pump, air conditioner | any | driver stub | later |
| EV charger | any | driver stub | later |

The router is a Keenetic/Netcraze with Entware on USB storage (aarch64, 1 GB RAM).

## How it works

```
thermostats, smart plugs ──(Tuya, LAN)──┐
phase meter, air sensors ──(MQTT)───────┤
                                        ▼
                         teplo on the router (Python, Django)
                         ├─ heating and load control loops
                         ├─ history in SQLite on USB storage
                         ├─ web panel (HTMX)
                         └─ Telegram alerts when online
```

Control policies are pure Python with no Django, Tuya or MQTT imports; use cases orchestrate them, and devices are reached through capability ports with one driver per protocol.
See [`docs/design.md`](docs/design.md) for the control logic, failure modes and the data model.

## Configuration

- **Site app.** A house is a Django app under `sites/<name>/` whose data migrations create zones, devices, schedules, tariffs, phase limits and the shedding priority. Copy `sites/example` to start your own.
- **Secrets.** Device addresses and local keys, the Telegram token and the panel password live in a secrets file on the router, outside the repository.

## Roadmap

1. Thermostats and schedules: night setback and timed morning pre-heat.
2. Phase meter: per-phase load management.
3. Air sensors: air-temperature regulation and learned optimal start.
4. Smart plugs: water heaters as sheddable loads, washing machine monitoring.

Each stage first runs in dry-run mode, logging the decisions it would make, before it is allowed to switch devices.
