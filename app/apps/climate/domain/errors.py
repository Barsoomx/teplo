from apps.core.domain.errors import DomainError


class ZoneNotFoundError(DomainError):
    def __init__(self, slug: str) -> None:
        super().__init__(f'zone not found: {slug}', error_code='zone_not_found')


class ComfortWindowNotFoundError(DomainError):
    def __init__(self, window_id: int) -> None:
        super().__init__(f'comfort window not found: {window_id}', error_code='comfort_window_not_found')


class RoomNotAdjustableError(DomainError):
    def __init__(self, slug: str) -> None:
        super().__init__(f'room not adjustable: {slug}', error_code='room_not_adjustable')
