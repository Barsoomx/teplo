from apps.core.domain.errors import DomainError


class AlertDeliveryError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, error_code='alert_delivery_failed')


class AlertRejectedError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, error_code='alert_rejected')


class TelegramNotConfiguredError(DomainError):
    def __init__(self) -> None:
        super().__init__(
            'the secrets file has no [telegram] table with a token and a chat_id',
            error_code='telegram_not_configured',
        )
