from apps.system.domain.clock import ClockStatus


class ClockStub:
    def __init__(self, trusted: bool) -> None:
        self._trusted = trusted

    def status(self) -> ClockStatus:
        return ClockStatus(trusted=self._trusted, reason='stub')
