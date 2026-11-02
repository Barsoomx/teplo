from datetime import timedelta
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

PROC_UPTIME = Path('/proc/uptime')


class UptimeReader:
    def __init__(self, path: Path) -> None:
        self._path = path

    @classmethod
    def init(cls) -> 'UptimeReader':
        return cls(path=PROC_UPTIME)

    def read(self) -> timedelta | None:
        try:
            return timedelta(seconds=float(self._path.read_text(encoding='ascii').split()[0]))
        except (OSError, IndexError, ValueError, OverflowError) as error:
            logger.warning(
                'router_uptime_unreadable',
                path=str(self._path),
                error=str(error),
            )
            return None
