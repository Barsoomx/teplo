from apps.house.domain.rooms import FloorRecord


class HouseStoreStub:
    def __init__(self, floors: list[FloorRecord]) -> None:
        self._floors = floors

    def floors(self) -> list[FloorRecord]:
        return list(self._floors)
