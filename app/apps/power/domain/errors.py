from apps.core.domain.errors import DomainError


class OutageNotFoundError(DomainError):
    def __init__(self, outage_id: int) -> None:
        super().__init__(f'outage not found: {outage_id}', error_code='outage_not_found')
