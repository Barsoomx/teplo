import re
import subprocess
from collections.abc import Callable

from django.conf import settings

from apps.system.domain.clock import ClockStatus, ClockStatusCode, ClockTrustSource

NDMC_COMMAND = ('/bin/ndmc', '-c', 'show ntp status')
SYNCHRONIZED_PATTERN = re.compile(r'synchronized:\s*(yes|no)')


class NdmsNtpSource:
    def __init__(self, run: Callable[[tuple[str, ...]], str]) -> None:
        self._run = run

    @classmethod
    def init(cls) -> 'NdmsNtpSource':
        return cls(run=_run_command)

    def status(self) -> ClockStatus:
        try:
            output = self._run(NDMC_COMMAND)
        except (OSError, subprocess.SubprocessError) as error:
            return ClockStatus(trusted=False, code=ClockStatusCode.NTP_STATUS_FAILED, reason=f'ndmc failed: {error}')

        match = SYNCHRONIZED_PATTERN.search(output)
        if match is None:
            return ClockStatus(trusted=False, code=ClockStatusCode.NTP_STATUS_MISSING, reason='ntp status missing')

        if match.group(1) != 'yes':
            return ClockStatus(trusted=False, code=ClockStatusCode.NTP_NOT_SYNCHRONIZED, reason='ntp not synchronized')

        return ClockStatus(trusted=True, code=ClockStatusCode.NTP_SYNCHRONIZED, reason='ntp synchronized')


class SystemClockSource:
    def status(self) -> ClockStatus:
        return ClockStatus(
            trusted=True,
            code=ClockStatusCode.TRUSTED_BY_CONFIGURATION,
            reason='system clock trusted by configuration',
        )


def clock_trust_source() -> ClockTrustSource:
    if settings.TEPLO_CLOCK_SOURCE == 'ndms':
        return NdmsNtpSource.init()

    return SystemClockSource()


def _run_command(command: tuple[str, ...]) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=5, check=True)
    return completed.stdout
