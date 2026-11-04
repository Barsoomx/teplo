from apps.core.domain.errors import DomainError

BROKER_UNAVAILABLE = 'broker_unavailable'


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


class PlugNotSwitchableError(DomainError):
    def __init__(self, slug: str, reason: str) -> None:
        super().__init__(f'plug {slug} cannot be switched: {reason}', error_code='plug_not_switchable')


class ClimateUnitNotControllableError(DomainError):
    def __init__(self, slug: str, reason: str) -> None:
        super().__init__(
            f'climate unit {slug} cannot be controlled: {reason}', error_code='climate_unit_not_controllable'
        )


class DeviceConfigError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, error_code='device_config_invalid')


class SensorReadingError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, error_code='sensor_reading_failed')


class BrokerUnavailableError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, error_code=BROKER_UNAVAILABLE)
