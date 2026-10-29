from abc import ABC, abstractmethod

import structlog

from apps.core.domain.errors import DomainError
from apps.core.domain.events import EventDispatcher, EventStore, event_dispatcher

logger = structlog.get_logger(__name__)


class BaseUseCase[TInput, TOutput](ABC):
    def __init__(self, dispatcher: EventDispatcher = event_dispatcher) -> None:
        self._dispatcher = dispatcher
        self._event_store = EventStore()

    def execute(self, input_dto: TInput) -> TOutput:
        with structlog.contextvars.bound_contextvars(use_case=type(self).__name__):
            try:
                output = self._execute(input_dto)
            except DomainError as error:
                self._event_store.pop_events()
                logger.warning(
                    'use_case_failed',
                    error_code=error.error_code,
                    detail=error.detail,
                )
                raise

            self._dispatcher.dispatch(self._event_store.pop_events())
            return output

    @abstractmethod
    def _execute(self, input_dto: TInput) -> TOutput: ...
