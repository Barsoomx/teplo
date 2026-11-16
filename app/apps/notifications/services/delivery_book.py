import threading


class DeliveryBook:
    def __init__(self) -> None:
        self._failing = False
        self._lock = threading.Lock()

    def fail(self) -> bool:
        with self._lock:
            starts = not self._failing
            self._failing = True
            return starts

    def recover(self) -> bool:
        with self._lock:
            ends = self._failing
            self._failing = False
            return ends


delivery_book = DeliveryBook()
