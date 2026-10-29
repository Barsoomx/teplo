from apps.core.domain.errors import DomainError, SecretMissingError


def test_domain_error_exposes_detail_and_code() -> None:
    error = DomainError('zone missing', error_code='zone_not_found')

    assert (error.detail, error.error_code, str(error)) == ('zone missing', 'zone_not_found', 'zone missing')


def test_domain_error_uses_default_code() -> None:
    assert DomainError('boom').error_code == 'domain_error'


def test_secret_missing_error_names_the_secret() -> None:
    error = SecretMissingError('thermostat-kitchen')

    assert (error.detail, error.error_code) == ('secret missing: thermostat-kitchen', 'secret_missing')
