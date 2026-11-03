from apps.core.domain.errors import DomainError


class OutageNotFoundError(DomainError):
    def __init__(self, outage_id: int) -> None:
        super().__init__(f'outage not found: {outage_id}', error_code='outage_not_found')


class LoadNotFoundError(DomainError):
    def __init__(self, slug: str) -> None:
        super().__init__(f'load not found: {slug}', error_code='load_not_found')
