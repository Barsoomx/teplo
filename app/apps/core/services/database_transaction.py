from contextlib import AbstractContextManager

from django.db import transaction


class DatabaseTransaction:
    def __call__(self) -> AbstractContextManager[None]:
        return transaction.atomic()
