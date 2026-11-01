from apps.core.domain.errors import DomainError


class DeviceUnavailableError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, error_code='device_unavailable')


class DeviceProtocolError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, error_code='device_protocol_error')


class DriverNotImplementedError(DomainError):
    def __init__(self, driver: str) -> None:
        super().__init__(f'driver not implemented: {driver}', error_code='driver_not_implemented')


class DeviceNotFoundError(DomainError):
    def __init__(self, slug: str) -> None:
        super().__init__(f'device not found: {slug}', error_code='device_not_found')


class DeviceConfigError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, error_code='device_config_invalid')
