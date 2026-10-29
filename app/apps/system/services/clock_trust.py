import re
import subprocess
from collections.abc import Callable

from django.conf import settings

from apps.system.domain.clock import ClockStatus, ClockTrustSource

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
            return ClockStatus(trusted=False, reason=f'ndmc failed: {error}')

        match = SYNCHRONIZED_PATTERN.search(output)
        if match is None:
            return ClockStatus(trusted=False, reason='ntp status missing')

        if match.group(1) != 'yes':
            return ClockStatus(trusted=False, reason='ntp not synchronized')

        return ClockStatus(trusted=True, reason='ntp synchronized')


class SystemClockSource:
    def status(self) -> ClockStatus:
        return ClockStatus(trusted=True, reason='system clock trusted by configuration')


def clock_trust_source() -> ClockTrustSource:
    if settings.TEPLO_CLOCK_SOURCE == 'ndms':
        return NdmsNtpSource.init()

    return SystemClockSource()


def _run_command(command: tuple[str, ...]) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=5, check=True)
    return completed.stdout
