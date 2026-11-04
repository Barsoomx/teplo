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


class ClockUntrustedError(DomainError):
    def __init__(self) -> None:
        super().__init__(
            'the clock is not synchronised, so guests would end at the wrong hour',
            error_code='clock_untrusted',
        )


class FireplaceNotFoundError(DomainError):
    def __init__(self, room: str | None) -> None:
        detail = 'no fireplace in the house' if room is None else f'no fireplace in the room: {room}'
        super().__init__(detail, error_code='fireplace_not_found')


class HeatSourceNotFoundError(DomainError):
    def __init__(self, slug: str) -> None:
        super().__init__(f'heat source not found: {slug}', error_code='heat_source_not_found')
