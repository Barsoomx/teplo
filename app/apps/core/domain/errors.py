from apps.core.domain.settings import SettingProblem


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


class SettingsRejectedError(DomainError):
    def __init__(self, problems: tuple[SettingProblem, ...]) -> None:
        super().__init__(
            'settings rejected: ' + ', '.join(f'{problem.field}:{problem.code}' for problem in problems),
            error_code='settings_rejected',
        )
        self._problems = problems

    @property
    def problems(self) -> tuple[SettingProblem, ...]:
        return self._problems
