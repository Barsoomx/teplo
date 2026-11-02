from collections.abc import Sequence
from datetime import datetime

from apps.climate.domain.drive import StartAnswer, ZoneStart


class LoadGateStub:
    def __init__(self, held: frozenset[str], answers: dict[str, StartAnswer]) -> None:
        self._held = held
        self._answers = answers
        self._asked: list[tuple[ZoneStart, ...]] = []

    @property
    def asked(self) -> list[tuple[ZoneStart, ...]]:
        return self._asked

    def release(self, zone: str) -> None:
        self._held = self._held - {zone}

    def held_zones(self) -> frozenset[str]:
        return self._held

    def ask_starts(self, starts: Sequence[ZoneStart], now: datetime) -> dict[str, StartAnswer]:  # noqa: ARG002
        self._asked.append(tuple(starts))
        return {start.zone: self._answers[start.zone] for start in starts if start.zone in self._answers}
