class DomainError(Exception):
    def __init__(self, detail: str, error_code: str = 'domain_error') -> None:
        super().__init__(detail)
        self._detail = detail
        self._error_code = error_code

    @property
    def detail(self) -> str:
        return self._detail

    @property
    def error_code(self) -> str:
        return self._error_code


class SecretMissingError(DomainError):
    def __init__(self, name: str) -> None:
        super().__init__(f'secret missing: {name}', error_code='secret_missing')
