import pytest
from pytest_django import Settings

from apps.system.domain.clock import ClockStatus, ClockStatusCode
from apps.system.services.clock_trust import NdmsNtpSource, SystemClockSource, clock_trust_source

SYNCHRONIZED_OUTPUT = (
    '\x1b[K\n           status: \n              elapsed: 74945\n               server: 1.ntp.netcraze.ru\n'
    '             accurate: yes\n         synchronized: yes\n'
)


def _synchronized(_command: tuple[str, ...]) -> str:
    return SYNCHRONIZED_OUTPUT


def _unsynchronized(_command: tuple[str, ...]) -> str:
    return SYNCHRONIZED_OUTPUT.replace('synchronized: yes', 'synchronized: no')


def _silent(_command: tuple[str, ...]) -> str:
    return ''


def _missing_ndmc(_command: tuple[str, ...]) -> str:
    raise FileNotFoundError('/bin/ndmc')


@pytest.fixture
def f_synchronized_source() -> NdmsNtpSource:
    return NdmsNtpSource(run=_synchronized)


@pytest.fixture
def f_unsynchronized_source() -> NdmsNtpSource:
    return NdmsNtpSource(run=_unsynchronized)


@pytest.fixture
def f_silent_source() -> NdmsNtpSource:
    return NdmsNtpSource(run=_silent)


@pytest.fixture
def f_missing_ndmc_source() -> NdmsNtpSource:
    return NdmsNtpSource(run=_missing_ndmc)


@pytest.fixture
def ndms_settings(settings: Settings) -> Settings:
    settings.TEPLO_CLOCK_SOURCE = 'ndms'
    return settings


def test_synchronized_ntp_is_trusted(f_synchronized_source: NdmsNtpSource) -> None:
    assert f_synchronized_source.status() == ClockStatus(
        trusted=True,
        code=ClockStatusCode.NTP_SYNCHRONIZED,
        reason='ntp synchronized',
    )


def test_unsynchronized_ntp_is_untrusted(f_unsynchronized_source: NdmsNtpSource) -> None:
    assert f_unsynchronized_source.status() == ClockStatus(
        trusted=False,
        code=ClockStatusCode.NTP_NOT_SYNCHRONIZED,
        reason='ntp not synchronized',
    )


def test_missing_status_is_untrusted(f_silent_source: NdmsNtpSource) -> None:
    assert f_silent_source.status() == ClockStatus(
        trusted=False,
        code=ClockStatusCode.NTP_STATUS_MISSING,
        reason='ntp status missing',
    )


def test_failing_ndmc_is_untrusted(f_missing_ndmc_source: NdmsNtpSource) -> None:
    assert f_missing_ndmc_source.status() == ClockStatus(
        trusted=False,
        code=ClockStatusCode.NTP_STATUS_FAILED,
        reason='ndmc failed: /bin/ndmc',
    )


def test_system_clock_is_trusted_by_configuration() -> None:
    assert SystemClockSource().status() == ClockStatus(
        trusted=True,
        code=ClockStatusCode.TRUSTED_BY_CONFIGURATION,
        reason='system clock trusted by configuration',
    )


def test_factory_uses_system_source_in_tests() -> None:
    assert isinstance(clock_trust_source(), SystemClockSource)


@pytest.mark.usefixtures('ndms_settings')
def test_factory_uses_ndms_source_on_router() -> None:
    assert isinstance(clock_trust_source(), NdmsNtpSource)
