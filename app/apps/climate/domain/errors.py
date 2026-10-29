from apps.core.domain.errors import DomainError


class ZoneNotFoundError(DomainError):
    def __init__(self, slug: str) -> None:
        super().__init__(f'zone not found: {slug}', error_code='zone_not_found')
