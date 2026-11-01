from apps.core.domain.errors import DomainError


class AlertDeliveryError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, error_code='alert_delivery_failed')


class AlertRejectedError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, error_code='alert_rejected')
