import uuid
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

PROC_BOOT_ID = Path('/proc/sys/kernel/random/boot_id')


class BootIdReader:
    def __init__(self, path: Path) -> None:
        self._path = path

    @classmethod
    def init(cls) -> 'BootIdReader':
        return cls(path=PROC_BOOT_ID)

    def read(self) -> str | None:
        try:
            return str(uuid.UUID(self._path.read_text(encoding='ascii').strip()))
        except (OSError, ValueError) as error:
            logger.warning(
                'router_boot_id_unreadable',
                path=str(self._path),
                error=str(error),
            )
            return None
