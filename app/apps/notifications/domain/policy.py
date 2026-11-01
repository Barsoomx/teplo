from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from apps.devices.domain.kinds import DeviceKind
from apps.notifications.domain.alerts import AlertKey, AlertKind, AlertTransition, MonitoredDevice, ZoneHeating

DEVICE_LOST_AFTER = timedelta(minutes=10)
DEVICE_LOSS_CONFIRMED_AFTER = timedelta(minutes=2)
ZONE_OFF_AFTER = timedelta(minutes=35)


@dataclass(frozen=True, kw_only=True)
class Condition:
    key: AlertKey
    name: str
    device_kind: DeviceKind
    failing: bool | None
    since: datetime | None
    raise_after: timedelta


@dataclass(frozen=True, kw_only=True)
class AlertTrack:
    since: datetime
    noticed_at: float
    raised: bool


@dataclass(frozen=True, kw_only=True)
class AlertChange:
    condition: Condition
    transition: AlertTransition
    since: datetime


@dataclass(frozen=True, kw_only=True)
class AlertEvaluation:
    tracks: dict[AlertKey, AlertTrack]
    changes: tuple[AlertChange, ...]


def device_condition(device: MonitoredDevice) -> Condition:
    return Condition(
        key=AlertKey(kind=AlertKind.DEVICE_LOST, subject=device.slug),
        name=device.name,
        device_kind=device.kind,
        failing=not device.available,
        since=device.last_seen_at,
        raise_after=DEVICE_LOST_AFTER if device.last_seen_at is None else DEVICE_LOSS_CONFIRMED_AFTER,
    )


def zone_condition(zone: ZoneHeating) -> Condition:
    return Condition(
        key=AlertKey(kind=AlertKind.ZONE_OFF, subject=zone.slug),
        name=zone.name,
        device_kind=DeviceKind.THERMOSTAT,
        failing=_zone_off(zone),
        since=None,
        raise_after=ZONE_OFF_AFTER,
    )


def evaluate_alerts(
    conditions: Sequence[Condition],
    tracks: Mapping[AlertKey, AlertTrack],
    now: datetime,
    monotonic: float,
) -> AlertEvaluation:
    next_tracks: dict[AlertKey, AlertTrack] = {}
    changes: list[AlertChange] = []
    for condition in conditions:
        track, change = _step(condition, tracks.get(condition.key), now, monotonic)
        if track is not None:
            next_tracks[condition.key] = track
        if change is not None:
            changes.append(change)
    return AlertEvaluation(tracks=next_tracks, changes=tuple(changes))


def _step(
    condition: Condition,
    track: AlertTrack | None,
    now: datetime,
    monotonic: float,
) -> tuple[AlertTrack | None, AlertChange | None]:
    if condition.failing is None:
        return track, None

    if not condition.failing:
        if track is None or not track.raised:
            return None, None

        return None, AlertChange(condition=condition, transition=AlertTransition.CLEARED, since=track.since)

    current = track or AlertTrack(since=condition.since or now, noticed_at=monotonic, raised=False)
    if current.raised or monotonic - current.noticed_at < condition.raise_after.total_seconds():
        return current, None

    return (
        replace(current, raised=True),
        AlertChange(condition=condition, transition=AlertTransition.RAISED, since=current.since),
    )


def _zone_off(zone: ZoneHeating) -> bool | None:
    if not zone.should_heat:
        return False

    if zone.power is None:
        return None

    return not zone.power
