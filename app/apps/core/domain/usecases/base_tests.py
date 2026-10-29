from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from apps.core.domain.errors import DomainError
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.domain.usecases.base import BaseUseCase

GREETED_AT = datetime(2026, 10, 29, tzinfo=UTC)


@dataclass(frozen=True, kw_only=True)
class Greeted(DomainEvent):
    name: str


@dataclass(frozen=True, kw_only=True)
class GreetInputDTO:
    name: str
    refuse: bool


class GreetUseCase(BaseUseCase[GreetInputDTO, str]):
    def _execute(self, input_dto: GreetInputDTO) -> str:
        self._event_store.add_event(Greeted(name=input_dto.name, occurred_at=GREETED_AT))
        if input_dto.refuse:
            raise DomainError('greeting refused', error_code='greeting_refused')

        return f'hello {input_dto.name}'


@pytest.fixture
def f_received() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_use_case(f_received: list[DomainEvent]) -> GreetUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(Greeted, f_received.append)
    return GreetUseCase(dispatcher=dispatcher)


def test_execute_returns_output_and_dispatches_events(
    f_use_case: GreetUseCase,
    f_received: list[DomainEvent],
) -> None:
    output = f_use_case.execute(GreetInputDTO(name='kitchen', refuse=False))

    assert (output, f_received) == ('hello kitchen', [Greeted(name='kitchen', occurred_at=GREETED_AT)])


def test_execute_drops_events_when_domain_error_raised(
    f_use_case: GreetUseCase,
    f_received: list[DomainEvent],
) -> None:
    with pytest.raises(DomainError, match='greeting refused'):
        f_use_case.execute(GreetInputDTO(name='kitchen', refuse=True))

    assert f_received == []
