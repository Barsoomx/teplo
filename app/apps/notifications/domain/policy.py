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
    began_at: float
    noticed_at: float | None
    raised: bool


@dataclass(frozen=True, kw_only=True)
class AlertChange:
    condition: Condition
    transition: AlertTransition
    since: datetime | None


@dataclass(frozen=True, kw_only=True)
class AlertEvaluation:
    tracks: dict[AlertKey, AlertTrack]
    changes: tuple[AlertChange, ...]


@dataclass(frozen=True, kw_only=True)
class _Clock:
    now: datetime
    monotonic: float
    trusted: bool

    def monotonic_of(self, wall: datetime | None) -> float:
        if wall is None:
            return self.monotonic

        return self.monotonic - (self.now - wall).total_seconds()

    def wall_of(self, moment: float) -> datetime | None:
        if not self.trusted:
            return None

        return self.now - timedelta(seconds=self.monotonic - moment)


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
    *,
    clock_trusted: bool,
) -> AlertEvaluation:
    clock = _Clock(now=now, monotonic=monotonic, trusted=clock_trusted)
    next_tracks: dict[AlertKey, AlertTrack] = {}
    changes: list[AlertChange] = []
    for condition in conditions:
        track, change = _step(condition, tracks.get(condition.key), clock)
        if track is not None:
            next_tracks[condition.key] = track
        if change is not None:
            changes.append(change)
    return AlertEvaluation(tracks=next_tracks, changes=tuple(changes))


def _step(
    condition: Condition,
    track: AlertTrack | None,
    clock: _Clock,
) -> tuple[AlertTrack | None, AlertChange | None]:
    if condition.failing is None:
        return _paused(track), None

    if not condition.failing:
        if track is None or not track.raised:
            return None, None

        return None, _change(condition, AlertTransition.CLEARED, track, clock)

    current = _running(condition, track, clock)
    if current.raised or not _due(current, condition.raise_after, clock.monotonic):
        return current, None

    raised = replace(current, raised=True)
    return raised, _change(condition, AlertTransition.RAISED, raised, clock)


def _paused(track: AlertTrack | None) -> AlertTrack | None:
    if track is None:
        return None

    return replace(track, noticed_at=None)


def _running(condition: Condition, track: AlertTrack | None, clock: _Clock) -> AlertTrack:
    if track is None:
        return AlertTrack(began_at=clock.monotonic_of(condition.since), noticed_at=clock.monotonic, raised=False)

    if track.noticed_at is None:
        return replace(track, noticed_at=clock.monotonic)

    return track


def _due(track: AlertTrack, raise_after: timedelta, monotonic: float) -> bool:
    return track.noticed_at is not None and monotonic - track.noticed_at >= raise_after.total_seconds()


def _change(condition: Condition, transition: AlertTransition, track: AlertTrack, clock: _Clock) -> AlertChange:
    return AlertChange(condition=condition, transition=transition, since=clock.wall_of(track.began_at))


def _zone_off(zone: ZoneHeating) -> bool | None:
    if not zone.should_heat:
        return False

    if zone.power is None:
        return None

    return not zone.power
