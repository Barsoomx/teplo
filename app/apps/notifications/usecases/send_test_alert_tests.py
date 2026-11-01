import pytest

from apps.notifications.domain.errors import AlertDeliveryError, TelegramNotConfiguredError
from apps.notifications.stubs import AlertSenderStub
from apps.notifications.usecases.send_test_alert import (
    SendTestAlertInputDTO,
    SendTestAlertOutputDTO,
    SendTestAlertUseCase,
)

TEXT = 'teplo: проверка связи. Оповещения будут приходить в этот чат'


@pytest.fixture
def f_sender() -> AlertSenderStub:
    return AlertSenderStub(errors={})


@pytest.fixture
def f_use_case(f_sender: AlertSenderStub) -> SendTestAlertUseCase:
    return SendTestAlertUseCase(sender=f_sender, language='ru')


@pytest.fixture
def f_offline_use_case() -> SendTestAlertUseCase:
    sender = AlertSenderStub(errors={TEXT: AlertDeliveryError('telegram unreachable: CERTIFICATE_VERIFY_FAILED')})
    return SendTestAlertUseCase(sender=sender, language='ru')


@pytest.fixture
def f_unconfigured_use_case() -> SendTestAlertUseCase:
    return SendTestAlertUseCase(sender=None, language='ru')


def test_check_message_goes_out_at_once_in_the_alert_language(
    f_use_case: SendTestAlertUseCase,
    f_sender: AlertSenderStub,
) -> None:
    output = f_use_case.execute(SendTestAlertInputDTO())

    assert (output, f_sender.sent) == (SendTestAlertOutputDTO(text=TEXT), [TEXT])


def test_failed_check_message_reports_the_reason(f_offline_use_case: SendTestAlertUseCase) -> None:
    with pytest.raises(AlertDeliveryError) as raised:
        f_offline_use_case.execute(SendTestAlertInputDTO())

    assert (raised.value.error_code, raised.value.detail) == (
        'alert_delivery_failed',
        'telegram unreachable: CERTIFICATE_VERIFY_FAILED',
    )


def test_check_message_without_telegram_settings_is_refused(f_unconfigured_use_case: SendTestAlertUseCase) -> None:
    with pytest.raises(TelegramNotConfiguredError) as raised:
        f_unconfigured_use_case.execute(SendTestAlertInputDTO())

    assert raised.value.error_code == 'telegram_not_configured'
