from apps.core.domain.errors import DomainError
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.readings import PresenceReading
from apps.devices.services.presence_store import PresenceStore, presence_store


class EsphomePresenceDriver:
    def __init__(self, slug: str, store: PresenceStore) -> None:
        self._slug = slug
        self._store = store

    @classmethod
    def init(cls, slug: str) -> 'EsphomePresenceDriver':
        return cls(slug=slug, store=presence_store)

    def read_presence(self) -> PresenceReading:
        snapshot = self._store.snapshot(self._slug)
        if (
            snapshot.available
            and snapshot.present is not None
            and snapshot.read_at is not None
            and snapshot.watched_for is not None
        ):
            return PresenceReading(
                present=snapshot.present,
                seen_ago=snapshot.seen_age,
                watched_for=snapshot.watched_for,
                read_at=snapshot.read_at,
            )

        if snapshot.error_code is None or snapshot.error is None:
            raise DeviceUnavailableError('no recent presence report')

        raise DomainError(snapshot.error, error_code=snapshot.error_code)
