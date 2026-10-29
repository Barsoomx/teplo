# Stage 1: Thermostats in Floor Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship teplo stage 1: one process on the Netcraze router that polls the five Tuya floor thermostats over the LAN, drives their floor setpoints from per-zone schedules (night setback, 3-hour timed pre-heat, boost), starts in dry-run, journals every decision and serves a password-protected dashboard on port 8088.

**Architecture:** altyn-style lightweight DDD on Django 5.2. Pure domain policies (schedule, floor policy, change planning, write guard) live in `climate/domain`; use cases (`PollThermostats`, `RunHeatingCycle`, `BoostZone`, telemetry use cases) orchestrate repositories, the in-memory thermostat state store and capability ports. One asyncio runtime runs periodic jobs in worker threads and serves the Django ASGI panel through uvicorn.

**Tech Stack:** Python 3.13, Django 5.2, Django REST framework 3.16, structlog, tinytuya, uvicorn, whitenoise, HTMX 2; pytest, pytest-django, factory_boy; ruff, mypy with django-stubs; Poetry; Docker for local commands; GitHub Actions; Entware on the router.

**Spec:** `docs/design.md` (sections 2, 4, 5, 6, 9, 10, 11, 12, 13). Conventions: `CLAUDE.md`.

## Global Constraints

- Follow `CLAUDE.md` exactly: single quotes, absolute imports from the `app/` root (`apps...`, `settings...`, `sites...`), no comments or docstrings except lint pragmas, private attributes with properties for reads, constructors only assign, defaults built in `init()` classmethods, a blank line after `return`/`raise` when code follows in the block, one view per file, tests next to the module as `<module>_tests.py`, fixtures prefixed `f_`/`m_` only when passed into a test, stubs over mocks except in view tests, every signature annotated.
- **Tests run only in CI.** Never run pytest locally. Allowed local commands, all in Docker: `docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'`, `docker compose run --rm --no-deps app python manage.py makemigrations ...`, `docker compose run --rm --no-deps app python manage.py check`. The TDD red phase is therefore not observed locally; tests and code land together and CI must end green.
- Work on branch `stage-1`. Every task ends with a commit, `git push origin stage-1` and `sh scripts/ci-watch.sh` finishing with a successful run.
- Public repository: device ids, device IP addresses, local keys and passwords never enter git. Real secrets live in `secrets/secrets.toml` (gitignored) and on the router.
- ET-44W data points: 1 power (bool), 2 mode (`hold` | `smart` | `holiday`), 16 setpoint (int, tenths of °C, 50–400), 24 floor temperature (int, tenths of °C); Tuya protocol 3.3.
- Write budget: at most one thermostat state change per zone per 30 minutes; boost resets the budget.
- Dry-run is the default: `TEPLO_DRY_RUN=true` or `Zone.live_control=False` keeps a command in the journal only.
- Schedule math runs on `timezone.localtime(...)`; `TIME_ZONE` defaults to `Europe/Moscow`.
- Panel port 8088. Router paths: `/opt/share/teplo`, `/opt/etc/teplo`, `/opt/var/lib/teplo`, `/opt/var/log/teplo`, `/opt/var/run`. SSH: `root@192.168.1.1`, port 222.
- Runtime dependencies are pure Python; `cryptography` comes from Entware's `python3-cryptography`.

**User decisions (already made):**
- "Вариант 1 ок": non-invasive hardware; the Tesla is not controlled.
- "должно работать без облака" and "Панелька тоже должна работать без интернета - по локалке": LAN-only runtime and panel.
- "можно что-то полегче, но думаю всё равно на роутере надо": HTMX panel served by teplo on the router.
- "репу сделал для красоты - можно там CI гонять": GitHub Actions on `Barsoomx/teplo`.
- "мои конфиги мы задаем как миграции бд": the house is `sites/barsoom` with data migrations.
- "заложить всякие нагревательные устройства сразу ... заглушками": capability ports for every device kind; unsupported drivers raise `DriverNotImplementedError`.
- "Код пиши нормально, как в алтыне - юзкейсы и тд, domain driven + SOLID": `CLAUDE.md` conventions.
- "ночной тариф нужно задать" and "Приоритет отключения тоже бы задавать": stages 2–4, outside this plan.

**Out of scope for stage 1:** air sensors and air regulation, learned optimal start, phase meter and load management, tariffs, Telegram alerts, smart plugs, clock offset taken from the phone, history charts, schedules and settings pages (the Django admin edits configuration), a Tuya key-fetch command.

## Review Focus

1. **Thermostat left in its weekly program** (`smart` mode, like Bathroom 1F today): teplo switches it to `hold` before writing a setpoint. Tests: Task 9 `test_plan_change_switches_smart_mode_to_hold`, Task 11 `test_cycle_switches_program_mode_to_hold`.
2. **Someone changes a setpoint on the thermostat or in Smart Life:** teplo reverts it at most once per 30 minutes, never in a write storm. Test: Task 11 `test_cycle_throttles_zone_commanded_recently`.
3. **Router rebooted without internet at night** (NTP not synchronized): zones hold comfort setpoints instead of trusting a wrong clock. Tests: Task 8 `test_unsynchronized_ntp_is_untrusted`, Task 11 `test_cycle_holds_comfort_when_clock_untrusted`.
4. **Comfort period or pre-heat crossing midnight** (comfort from 01:00, pre-heat from 22:00 the day before). Test: Task 9 `test_preheat_starts_on_previous_day`.
5. **A configured device without secrets, or with an unsupported driver:** polling records the failure, the other thermostats keep working, the dashboard shows the error. Tests: Task 6 `test_missing_secret_raises`, Task 6 `test_unsupported_driver_raises`, Task 7 `test_poll_records_states_and_failures`.

---

### Task 1: Project skeleton, tooling and CI

**Goal:** A Django 5.2 project skeleton with Poetry, Docker, ruff, mypy, pytest and GitHub Actions that passes CI.

**Files:**
- Create: `pyproject.toml`, `poetry.lock` (generated), `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.github/workflows/ci.yml`, `scripts/ci-watch.sh`
- Create: `app/manage.py`, `app/settings/__init__.py`, `app/settings/settings.py`, `app/settings/test_settings.py`, `app/settings/urls.py`, `app/settings/asgi.py`, `app/settings/utils.py`, `app/settings/utils_tests.py`
- Create: `app/apps/__init__.py`, `app/sites/__init__.py`, and for each of `core`, `devices`, `system`, `climate`, `telemetry`, `panel`: `app/apps/<name>/__init__.py`, `app/apps/<name>/apps.py`
- Modify: `.gitignore`

**Acceptance Criteria:**
- [ ] `docker compose run --rm --no-deps app python manage.py check` prints `System check identified no issues (0 silenced).`
- [ ] The CI run for the pushed commit on `stage-1` succeeds with ruff lint, ruff format, mypy, `makemigrations --check` and pytest (the `settings/utils_tests.py` tests).

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → the run ends with `completed with 'success'`.

**Steps:**

- [ ] **Step 1: Create the branch**

```bash
git checkout -b stage-1
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = 'teplo'
version = '0.1.0'
description = 'Local-first heating and load controller for a home router'
requires-python = '>=3.13,<3.14'
dependencies = [
    'django>=5.2,<5.3',
    'djangorestframework>=3.16,<3.17',
    'structlog>=25.1',
    'tinytuya>=1.17,<2',
    'uvicorn>=0.34',
    'whitenoise>=6.9',
]

[tool.poetry]
package-mode = false

[tool.poetry.group.dev.dependencies]
pytest = '>=8.3'
pytest-django = '>=4.10'
factory-boy = '>=3.3'
freezegun = '>=1.5'
ruff = '>=0.11'
mypy = '>=1.15'
django-stubs = { version = '>=5.2', extras = ['compatible-mypy'] }
djangorestframework-stubs = { version = '>=3.15', extras = ['compatible-mypy'] }

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = 'settings.test_settings'
pythonpath = ['app']
testpaths = ['app']
python_files = ['*_tests.py']
addopts = '--strict-markers'

[tool.ruff]
line-length = 120
target-version = 'py313'
src = ['app']
extend-exclude = ['app/apps/*/migrations', 'app/sites/*/migrations']

[tool.ruff.format]
quote-style = 'single'

[tool.ruff.lint]
select = ['W', 'ARG', 'ANN', 'ASYNC', 'B', 'COM', 'C4', 'C90', 'DTZ', 'E', 'ERA', 'F', 'UP', 'T10', 'T20', 'Q', 'S', 'I', 'N', 'YTT', 'PTH']
ignore = ['COM812']

[tool.ruff.lint.flake8-quotes]
inline-quotes = 'single'

[tool.ruff.lint.isort]
known-first-party = ['apps', 'settings', 'sites']

[tool.ruff.lint.per-file-ignores]
'*_tests.py' = ['S101', 'S105', 'S106']
'app/settings/*' = ['S105']
'app/settings/test_settings.py' = ['F403', 'F405']
'app/apps/system/services/clock_trust.py' = ['S603']

[tool.mypy]
python_version = '3.13'
mypy_path = 'app'
explicit_package_bases = true
disallow_untyped_defs = true
warn_unused_ignores = true
plugins = ['mypy_django_plugin.main', 'mypy_drf_plugin.main']
exclude = ['/migrations/']

[[tool.mypy.overrides]]
module = ['tinytuya', 'tinytuya.*', 'factory', 'factory.*']
ignore_missing_imports = true

[tool.django-stubs]
django_settings_module = 'settings.test_settings'
```

- [ ] **Step 3: Write `Dockerfile`, `.dockerignore`, `docker-compose.yml`**

`Dockerfile`:

```dockerfile
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir poetry poetry-plugin-export

WORKDIR /code
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root

WORKDIR /code/app
```

`.dockerignore`:

```
.git
.local
secrets
spike
.venv
**/__pycache__
.mypy_cache
.ruff_cache
.pytest_cache
```

`docker-compose.yml`:

```yaml
services:
  app:
    build: .
    image: teplo-dev
    volumes:
      - .:/code
    environment:
      DJANGO_SETTINGS_MODULE: settings.settings
      TEPLO_SITE: ${TEPLO_SITE:-}
      TEPLO_DB_PATH: /code/.local/teplo.sqlite3
      TEPLO_STATIC_ROOT: /code/.local/static
      TEPLO_SECRETS_FILE: /code/secrets/secrets.toml
      TEPLO_CLOCK_SOURCE: system
      TEPLO_DRY_RUN: 'true'
      TEPLO_PANEL_HOST: 0.0.0.0
      TEPLO_ALLOWED_HOSTS: localhost,127.0.0.1
    ports:
      - '8088:8088'
```

- [ ] **Step 4: Write the settings package and `manage.py`**

`app/settings/__init__.py`: empty file.

`app/settings/utils.py`:

```python
def to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default

    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def to_list(value: str | None) -> list[str]:
    if not value:
        return []

    return [item.strip() for item in value.split(',') if item.strip()]
```

`app/settings/settings.py`:

```python
import os
from pathlib import Path

import structlog

from settings.utils import to_bool, to_list

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_DIR = BASE_DIR.parent / '.local'

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'teplo-development-only')
DEBUG = to_bool(os.getenv('DJANGO_DEBUG'))
ALLOWED_HOSTS = to_list(os.getenv('TEPLO_ALLOWED_HOSTS')) or ['localhost', '127.0.0.1']

TEPLO_SITE = os.getenv('TEPLO_SITE', '')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'apps.core',
    'apps.devices',
    'apps.system',
    'apps.climate',
    'apps.telemetry',
    'apps.panel',
    *([f'sites.{TEPLO_SITE}'] if TEPLO_SITE else []),
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'settings.urls'
ASGI_APPLICATION = 'settings.asgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.getenv('TEPLO_DB_PATH', str(LOCAL_DIR / 'teplo.sqlite3')),
        'OPTIONS': {'timeout': 20},
    },
}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.getenv('TEPLO_TIME_ZONE', 'Europe/Moscow')
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = os.getenv('TEPLO_STATIC_ROOT', str(LOCAL_DIR / 'static'))
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

LOGIN_URL = 'panel:login'
LOGIN_REDIRECT_URL = 'panel:dashboard'
LOGOUT_REDIRECT_URL = 'panel:login'
SESSION_COOKIE_AGE = 60 * 60 * 24 * 365

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
}

TEPLO_SECRETS_FILE = os.getenv('TEPLO_SECRETS_FILE', '/opt/etc/teplo/secrets.toml')
TEPLO_CLOCK_SOURCE = os.getenv('TEPLO_CLOCK_SOURCE', 'ndms')
TEPLO_DRY_RUN = to_bool(os.getenv('TEPLO_DRY_RUN'), default=True)
TEPLO_PANEL_HOST = os.getenv('TEPLO_PANEL_HOST', '127.0.0.1')
TEPLO_PANEL_PORT = int(os.getenv('TEPLO_PANEL_PORT', '8088'))

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt='iso'),
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer(ensure_ascii=False),
    ],
)
```

`app/settings/test_settings.py`:

```python
from settings.settings import *

INSTALLED_APPS = [app for app in INSTALLED_APPS if not app.startswith('sites.')]
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
TEPLO_SITE = ''
TEPLO_SECRETS_FILE = '/nonexistent/teplo/secrets.toml'
TEPLO_CLOCK_SOURCE = 'system'
TEPLO_DRY_RUN = True
```

`app/settings/urls.py`:

```python
from django.contrib import admin
from django.urls import path

urlpatterns = [
    path('admin/', admin.site.urls),
]
```

`app/settings/asgi.py`:

```python
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings.settings')

application = get_asgi_application()
```

`app/manage.py`:

```python
import os
import sys

from django.core.management import execute_from_command_line


def main() -> None:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings.settings')
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
```

- [ ] **Step 5: Create the app shells**

`app/apps/__init__.py` and `app/sites/__init__.py`: empty files. For every name below create an empty `app/apps/<name>/__init__.py` and `app/apps/<name>/apps.py` with the shown class:

| `<name>` | class |
|---|---|
| core | `CoreConfig` |
| devices | `DevicesConfig` |
| system | `SystemConfig` |
| climate | `ClimateConfig` |
| telemetry | `TelemetryConfig` |
| panel | `PanelConfig` |

`app/apps/climate/apps.py` (the others differ only in class name and `name`):

```python
from django.apps import AppConfig


class ClimateConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.climate'
```

- [ ] **Step 6: Write the settings tests**

`app/settings/utils_tests.py`:

```python
import pytest

from settings.utils import to_bool, to_list


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('true', True),
        ('YES', True),
        (' 1 ', True),
        ('on', True),
        ('false', False),
        ('0', False),
        ('', False),
    ],
)
def test_to_bool_parses_known_values(raw: str, expected: bool) -> None:
    assert to_bool(raw) is expected


def test_to_bool_returns_default_for_missing_value() -> None:
    assert to_bool(None, default=True) is True


def test_to_list_splits_and_strips() -> None:
    assert to_list('a, b,,c ') == ['a', 'b', 'c']


def test_to_list_returns_empty_for_missing_value() -> None:
    assert to_list(None) == []
```

- [ ] **Step 7: Write CI, the CI watcher and ignore rules**

`.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
  pull_request:

jobs:
  checks:
    runs-on: ubuntu-latest
    env:
      DJANGO_SETTINGS_MODULE: settings.test_settings
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
      - name: Install Poetry
        run: pipx install poetry
      - name: Install dependencies
        run: poetry install --no-root --no-interaction
      - name: Ruff lint
        run: poetry run ruff check .
      - name: Ruff format
        run: poetry run ruff format --check .
      - name: Mypy
        run: poetry run mypy app
      - name: Migrations are up to date
        run: poetry run python app/manage.py makemigrations --check --dry-run
      - name: Tests
        run: poetry run pytest
```

`scripts/ci-watch.sh`:

```sh
#!/bin/sh
set -eu

sha=$(git rev-parse HEAD)
run_id=''
attempt=0
while [ -z "$run_id" ] && [ "$attempt" -lt 30 ]; do
    run_id=$(gh run list --commit "$sha" --limit 1 --json databaseId --jq '.[0].databaseId // empty')
    [ -n "$run_id" ] || sleep 4
    attempt=$((attempt + 1))
done
[ -n "$run_id" ] || { echo "no CI run for $sha" >&2; exit 1; }
gh run watch "$run_id" --exit-status
```

Append to `.gitignore`:

```
.local/
requirements.txt
```

- [ ] **Step 8: Generate the lock file and check Django**

```bash
docker compose build app
docker compose run --rm --no-deps app sh -c 'cd /code && poetry lock'
docker compose build app
docker compose run --rm --no-deps app python manage.py check
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
```

Expected: `System check identified no issues (0 silenced).`, ruff `All checks passed!`, mypy `Success: no issues found`.

- [ ] **Step 9: Commit, push, watch CI**

```bash
git add pyproject.toml poetry.lock Dockerfile .dockerignore docker-compose.yml .github scripts .gitignore app
git commit -m 'Add Django skeleton, tooling and CI'
git push -u origin stage-1
sh scripts/ci-watch.sh
```

Expected: the run completes with success.

```json:metadata
{"files": ["pyproject.toml", "poetry.lock", "Dockerfile", ".dockerignore", "docker-compose.yml", ".github/workflows/ci.yml", "scripts/ci-watch.sh", "app/manage.py", "app/settings/settings.py", "app/settings/test_settings.py", "app/settings/urls.py", "app/settings/asgi.py", "app/settings/utils.py", "app/settings/utils_tests.py", ".gitignore"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["manage.py check reports no issues in Docker", "CI run on stage-1 succeeds with ruff, format, mypy, makemigrations --check and pytest"], "modelTier": "standard"}
```

---

### Task 2: Core kernel — errors, domain events, base use case

**Goal:** `DomainError`, `DomainEvent` with an event store and dispatcher, and `BaseUseCase` with `execute`/`_execute`, covered by tests.

**Files:**
- Create: `app/apps/core/domain/__init__.py`, `app/apps/core/domain/errors.py`, `app/apps/core/domain/errors_tests.py`
- Create: `app/apps/core/domain/events.py`, `app/apps/core/domain/events_tests.py`
- Create: `app/apps/core/domain/usecases/__init__.py`, `app/apps/core/domain/usecases/base.py`, `app/apps/core/domain/usecases/base_tests.py`

**Acceptance Criteria:**
- [ ] `BaseUseCase.execute` returns `_execute` output and dispatches collected events after success.
- [ ] A `DomainError` from `_execute` propagates and its events are dropped.
- [ ] A failing event handler does not stop other handlers.
- [ ] CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success; pytest collects `errors_tests.py`, `events_tests.py`, `base_tests.py`.

**Steps:**

- [ ] **Step 1: Write the tests**

`app/apps/core/domain/__init__.py` and `app/apps/core/domain/usecases/__init__.py`: empty files.

`app/apps/core/domain/errors_tests.py`:

```python
from apps.core.domain.errors import DomainError, SecretMissingError


def test_domain_error_exposes_detail_and_code() -> None:
    error = DomainError('zone missing', error_code='zone_not_found')

    assert (error.detail, error.error_code, str(error)) == ('zone missing', 'zone_not_found', 'zone missing')


def test_domain_error_uses_default_code() -> None:
    assert DomainError('boom').error_code == 'domain_error'


def test_secret_missing_error_names_the_secret() -> None:
    error = SecretMissingError('thermostat-kitchen')

    assert (error.detail, error.error_code) == ('secret missing: thermostat-kitchen', 'secret_missing')
```

`app/apps/core/domain/events_tests.py`:

```python
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from apps.core.domain.events import DomainEvent, EventDispatcher, EventStore


@dataclass(frozen=True, kw_only=True)
class ZoneTouched(DomainEvent):
    zone: str


@dataclass(frozen=True, kw_only=True)
class ZoneIgnored(DomainEvent):
    zone: str


def _failing_handler(event: DomainEvent) -> None:
    raise RuntimeError(f'cannot handle {event}')


@pytest.fixture
def f_event() -> ZoneTouched:
    return ZoneTouched(zone='kitchen', occurred_at=datetime(2026, 10, 29, 6, 0, tzinfo=UTC))


@pytest.fixture
def f_store(f_event: ZoneTouched) -> EventStore:
    store = EventStore()
    store.add_event(f_event)
    return store


@pytest.fixture
def f_received() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_ignored() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_dispatcher(f_received: list[DomainEvent], f_ignored: list[DomainEvent]) -> EventDispatcher:
    dispatcher = EventDispatcher()
    dispatcher.register(ZoneTouched, _failing_handler)
    dispatcher.register(ZoneTouched, f_received.append)
    dispatcher.register(ZoneIgnored, f_ignored.append)
    return dispatcher


def test_event_store_pops_events_once(f_store: EventStore, f_event: ZoneTouched) -> None:
    first = f_store.pop_events()
    second = f_store.pop_events()

    assert (first, second) == ((f_event,), ())


def test_dispatcher_delivers_to_matching_handlers_despite_failure(
    f_dispatcher: EventDispatcher,
    f_event: ZoneTouched,
    f_received: list[DomainEvent],
    f_ignored: list[DomainEvent],
) -> None:
    f_dispatcher.dispatch([f_event])

    assert (f_received, f_ignored) == ([f_event], [])
```

`app/apps/core/domain/usecases/base_tests.py`:

```python
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from apps.core.domain.errors import DomainError
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.domain.usecases.base import BaseUseCase

GREETED_AT = datetime(2026, 10, 29, tzinfo=UTC)


@dataclass(frozen=True, kw_only=True)
class Greeted(DomainEvent):
    name: str


@dataclass(frozen=True, kw_only=True)
class GreetInputDTO:
    name: str
    refuse: bool


class GreetUseCase(BaseUseCase[GreetInputDTO, str]):
    def _execute(self, input_dto: GreetInputDTO) -> str:
        self._event_store.add_event(Greeted(name=input_dto.name, occurred_at=GREETED_AT))
        if input_dto.refuse:
            raise DomainError('greeting refused', error_code='greeting_refused')

        return f'hello {input_dto.name}'


@pytest.fixture
def f_received() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_use_case(f_received: list[DomainEvent]) -> GreetUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(Greeted, f_received.append)
    return GreetUseCase(dispatcher=dispatcher)


def test_execute_returns_output_and_dispatches_events(
    f_use_case: GreetUseCase,
    f_received: list[DomainEvent],
) -> None:
    output = f_use_case.execute(GreetInputDTO(name='kitchen', refuse=False))

    assert (output, f_received) == ('hello kitchen', [Greeted(name='kitchen', occurred_at=GREETED_AT)])


def test_execute_drops_events_when_domain_error_raised(
    f_use_case: GreetUseCase,
    f_received: list[DomainEvent],
) -> None:
    with pytest.raises(DomainError, match='greeting refused'):
        f_use_case.execute(GreetInputDTO(name='kitchen', refuse=True))

    assert f_received == []
```

- [ ] **Step 2: Write the implementation**

`app/apps/core/domain/errors.py`:

```python
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
```

`app/apps/core/domain/events.py`:

```python
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import cast

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    occurred_at: datetime


EventHandler = Callable[[DomainEvent], None]


class EventStore:
    def __init__(self) -> None:
        self._events: list[DomainEvent] = []

    def add_event(self, event: DomainEvent) -> None:
        self._events.append(event)

    def pop_events(self) -> tuple[DomainEvent, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events


class EventDispatcher:
    def __init__(self) -> None:
        self._handlers: defaultdict[type[DomainEvent], list[EventHandler]] = defaultdict(list)

    def register(self, event_type: type[DomainEvent], handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    def dispatch(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            for handler in self._handlers[type(event)]:
                self._call(handler, event)

    @staticmethod
    def _call(handler: EventHandler, event: DomainEvent) -> None:
        try:
            handler(event)
        except Exception:
            logger.exception(
                'domain_event_handler_failed',
                event=type(event).__name__,
                handler=getattr(handler, '__qualname__', repr(handler)),
            )


event_dispatcher = EventDispatcher()


def domain_event_handler[TEvent: DomainEvent](
    event_type: type[TEvent],
) -> Callable[[Callable[[TEvent], None]], Callable[[TEvent], None]]:
    def register(handler: Callable[[TEvent], None]) -> Callable[[TEvent], None]:
        event_dispatcher.register(event_type, cast(EventHandler, handler))
        return handler

    return register
```

`app/apps/core/domain/usecases/base.py`:

```python
from abc import ABC, abstractmethod

import structlog

from apps.core.domain.errors import DomainError
from apps.core.domain.events import EventDispatcher, EventStore, event_dispatcher

logger = structlog.get_logger(__name__)


class BaseUseCase[TInput, TOutput](ABC):
    def __init__(self, dispatcher: EventDispatcher = event_dispatcher) -> None:
        self._dispatcher = dispatcher
        self._event_store = EventStore()

    def execute(self, input_dto: TInput) -> TOutput:
        with structlog.contextvars.bound_contextvars(use_case=type(self).__name__):
            try:
                output = self._execute(input_dto)
            except DomainError as error:
                self._event_store.pop_events()
                logger.warning(
                    'use_case_failed',
                    error_code=error.error_code,
                    detail=error.detail,
                )
                raise

            self._dispatcher.dispatch(self._event_store.pop_events())
            return output

    @abstractmethod
    def _execute(self, input_dto: TInput) -> TOutput: ...
```

- [ ] **Step 3: Lint and type-check in Docker**

```bash
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
```

Expected: no findings.

- [ ] **Step 4: Commit, push, watch CI**

```bash
git add app/apps/core/domain
git commit -m 'Add core domain errors, events and base use case'
git push origin stage-1
sh scripts/ci-watch.sh
```

```json:metadata
{"files": ["app/apps/core/domain/errors.py", "app/apps/core/domain/events.py", "app/apps/core/domain/usecases/base.py", "app/apps/core/domain/errors_tests.py", "app/apps/core/domain/events_tests.py", "app/apps/core/domain/usecases/base_tests.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["execute returns output and dispatches events after success", "DomainError propagates and its events are dropped", "a failing handler does not stop other handlers", "CI green"], "modelTier": "mechanical"}
```

---

### Task 3: Architecture boundary tests

**Goal:** AST tests that fail when a domain module imports Django, DRF, a device library, a network client or another app's models, repositories, services, drivers, views, serializers, management or use cases.

**Files:**
- Create: `app/apps/core/architecture_tests.py`

**Acceptance Criteria:**
- [ ] Every `app/apps/*/domain/**/*.py` module (excluding `*_tests.py`) is a separate parametrized case.
- [ ] `_is_forbidden` returns True for `django.db` and `apps.devices.models.device`, False for `apps.climate.domain.schedule` and `datetime`.
- [ ] CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Write the test module**

`app/apps/core/architecture_tests.py`:

```python
import ast
from pathlib import Path

import pytest

APPS_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_PACKAGES = ('django', 'rest_framework', 'tinytuya', 'paho', 'requests', 'uvicorn', 'whitenoise')
FORBIDDEN_LAYERS = (
    'models',
    'repositories',
    'services',
    'drivers',
    'views',
    'serializers',
    'management',
    'usecases',
)


def _domain_modules() -> list[Path]:
    return sorted(path for path in APPS_ROOT.glob('*/domain/**/*.py') if not path.name.endswith('_tests.py'))


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _is_forbidden(module: str) -> bool:
    parts = module.split('.')
    if parts[0] in FORBIDDEN_PACKAGES:
        return True

    return parts[0] == 'apps' and len(parts) > 2 and parts[2] in FORBIDDEN_LAYERS


@pytest.mark.parametrize('module_path', _domain_modules(), ids=lambda path: str(path.relative_to(APPS_ROOT)))
def test_domain_module_imports_only_pure_code(module_path: Path) -> None:
    forbidden = [module for module in _imports(module_path) if _is_forbidden(module)]

    assert forbidden == []


@pytest.mark.parametrize(
    ('module', 'expected'),
    [
        ('django.db', True),
        ('apps.devices.models.device', True),
        ('apps.climate.domain.schedule', False),
        ('datetime', False),
    ],
)
def test_forbidden_import_rules(module: str, expected: bool) -> None:
    assert _is_forbidden(module) is expected
```

- [ ] **Step 2: Lint, commit, push, watch CI**

```bash
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/apps/core/architecture_tests.py
git commit -m 'Add architecture tests for pure domain modules'
git push origin stage-1
sh scripts/ci-watch.sh
```

```json:metadata
{"files": ["app/apps/core/architecture_tests.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["each domain module is a parametrized case", "_is_forbidden classifies django.db and apps.devices.models.device as forbidden and apps.climate.domain.schedule and datetime as allowed", "CI green"], "modelTier": "mechanical"}
```

---

### Task 4: Devices domain — readings, capability ports, errors, events

**Goal:** Value objects and `Protocol` ports for every device kind (thermostat, plug, temperature sensor, phase meter, climate unit, EV charger), device kinds and drivers, device errors and the availability event.

**Files:**
- Create: `app/apps/devices/domain/__init__.py`, `readings.py`, `capabilities.py`, `kinds.py`, `errors.py`, `errors_tests.py`, `events.py` (all under `app/apps/devices/domain/`)

**Acceptance Criteria:**
- [ ] `capabilities.py` declares `FloorThermostat`, `SwitchablePlug`, `TemperatureSensor`, `PhaseMeter`, `ClimateUnit`, `EvCharger`, `ThermostatProvider`.
- [ ] Each device error carries its own `error_code`.
- [ ] Architecture tests pass for the new modules; CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Write the error tests**

`app/apps/devices/domain/__init__.py`: empty file.

`app/apps/devices/domain/errors_tests.py`:

```python
import pytest

from apps.core.domain.errors import DomainError
from apps.devices.domain.errors import (
    DeviceNotFoundError,
    DeviceProtocolError,
    DeviceUnavailableError,
    DriverNotImplementedError,
)


@pytest.mark.parametrize(
    ('error', 'code', 'detail'),
    [
        (DeviceUnavailableError('timeout'), 'device_unavailable', 'timeout'),
        (DeviceProtocolError('bad payload'), 'device_protocol_error', 'bad payload'),
        (DriverNotImplementedError('unsupported'), 'driver_not_implemented', 'driver not implemented: unsupported'),
        (DeviceNotFoundError('thermostat-attic'), 'device_not_found', 'device not found: thermostat-attic'),
    ],
)
def test_device_errors_carry_codes(error: DomainError, code: str, detail: str) -> None:
    assert (error.error_code, error.detail) == (code, detail)
```

- [ ] **Step 2: Write the domain modules**

`app/apps/devices/domain/readings.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ThermostatMode(StrEnum):
    HOLD = 'hold'
    SMART = 'smart'
    HOLIDAY = 'holiday'


class ClimateMode(StrEnum):
    OFF = 'off'
    HEAT = 'heat'
    COOL = 'cool'
    AUTO = 'auto'


class Phase(StrEnum):
    A = 'a'
    B = 'b'
    C = 'c'


@dataclass(frozen=True, kw_only=True)
class ThermostatState:
    power: bool
    mode: ThermostatMode
    setpoint: Decimal
    floor_temperature: Decimal
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class PlugState:
    on: bool
    power_watts: Decimal
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class TemperatureReading:
    temperature: Decimal
    humidity: Decimal | None
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class PhaseCurrents:
    amps: dict[Phase, Decimal]
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class ClimateUnitState:
    mode: ClimateMode
    target: Decimal
    room_temperature: Decimal | None
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class EvChargerState:
    charging: bool
    current_limit_amps: Decimal
    read_at: datetime
```

`app/apps/devices/domain/capabilities.py`:

```python
from decimal import Decimal
from typing import Protocol

from apps.devices.domain.readings import (
    ClimateMode,
    ClimateUnitState,
    EvChargerState,
    PhaseCurrents,
    PlugState,
    TemperatureReading,
    ThermostatMode,
    ThermostatState,
)


class FloorThermostat(Protocol):
    def read_state(self) -> ThermostatState: ...

    def set_power(self, power: bool) -> None: ...

    def set_mode(self, mode: ThermostatMode) -> None: ...

    def set_setpoint(self, setpoint: Decimal) -> None: ...


class SwitchablePlug(Protocol):
    def read_state(self) -> PlugState: ...

    def set_on(self, on: bool) -> None: ...


class TemperatureSensor(Protocol):
    def read_temperature(self) -> TemperatureReading: ...


class PhaseMeter(Protocol):
    def read_currents(self) -> PhaseCurrents: ...


class ClimateUnit(Protocol):
    def read_state(self) -> ClimateUnitState: ...

    def set_target(self, mode: ClimateMode, target: Decimal) -> None: ...


class EvCharger(Protocol):
    def read_state(self) -> EvChargerState: ...

    def set_current_limit(self, amps: Decimal) -> None: ...


class ThermostatProvider(Protocol):
    def thermostat(self, slug: str) -> FloorThermostat: ...
```

`app/apps/devices/domain/kinds.py`:

```python
from enum import StrEnum


class DeviceKind(StrEnum):
    THERMOSTAT = 'thermostat'
    PLUG = 'plug'
    TEMPERATURE_SENSOR = 'temperature_sensor'
    PHASE_METER = 'phase_meter'
    CLIMATE_UNIT = 'climate_unit'
    EV_CHARGER = 'ev_charger'


class DeviceDriver(StrEnum):
    TUYA_THERMOSTAT = 'tuya_thermostat'
    TUYA_PLUG = 'tuya_plug'
    ESPHOME_TEMPERATURE = 'esphome_temperature'
    ESPHOME_PHASE_METER = 'esphome_phase_meter'
    UNSUPPORTED = 'unsupported'
```

`app/apps/devices/domain/errors.py`:

```python
from apps.core.domain.errors import DomainError


class DeviceUnavailableError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, error_code='device_unavailable')


class DeviceProtocolError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, error_code='device_protocol_error')


class DriverNotImplementedError(DomainError):
    def __init__(self, driver: str) -> None:
        super().__init__(f'driver not implemented: {driver}', error_code='driver_not_implemented')


class DeviceNotFoundError(DomainError):
    def __init__(self, slug: str) -> None:
        super().__init__(f'device not found: {slug}', error_code='device_not_found')
```

`app/apps/devices/domain/events.py`:

```python
from dataclasses import dataclass

from apps.core.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class DeviceAvailabilityChanged(DomainEvent):
    slug: str
    available: bool
    reason: str
```

- [ ] **Step 3: Lint, commit, push, watch CI**

```bash
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/apps/devices/domain
git commit -m 'Add device capability ports, readings and errors'
git push origin stage-1
sh scripts/ci-watch.sh
```

```json:metadata
{"files": ["app/apps/devices/domain/readings.py", "app/apps/devices/domain/capabilities.py", "app/apps/devices/domain/kinds.py", "app/apps/devices/domain/errors.py", "app/apps/devices/domain/errors_tests.py", "app/apps/devices/domain/events.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["capabilities declares FloorThermostat, SwitchablePlug, TemperatureSensor, PhaseMeter, ClimateUnit, EvCharger, ThermostatProvider", "each device error carries its own error_code", "architecture tests pass; CI green"], "modelTier": "mechanical"}
```

---

### Task 5: Device model, repository and secrets file

**Goal:** The `Device` model with its migration, admin and factory, `DeviceRepository`, and `SecretsFile` reading device secrets and the panel password from TOML.

**Files:**
- Create: `app/apps/core/services/__init__.py`, `app/apps/core/services/secrets.py`, `app/apps/core/services/secrets_tests.py`
- Create: `app/apps/devices/models/__init__.py`, `app/apps/devices/models/device.py`
- Create: `app/apps/devices/migrations/__init__.py`, `app/apps/devices/migrations/0001_initial.py` (generated)
- Create: `app/apps/devices/repositories/__init__.py`, `app/apps/devices/repositories/device.py`, `app/apps/devices/repositories/device_tests.py`
- Create: `app/apps/devices/admin.py`, `app/apps/devices/factories.py`

**Acceptance Criteria:**
- [ ] `SecretsFile.init(path)` reads `[devices.<slug>]` and `[panel] password`; a missing file yields no secrets; `device(slug)` raises `SecretMissingError` for unknown slugs.
- [ ] `DeviceRepository.thermostat_slugs()` returns only thermostats ordered by slug; `get()` raises `DeviceNotFoundError`.
- [ ] `makemigrations --check` passes in CI; CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Write the tests**

`app/apps/core/services/__init__.py`: empty file.

`app/apps/core/services/secrets_tests.py`:

```python
from pathlib import Path

import pytest

from apps.core.domain.errors import SecretMissingError
from apps.core.services.secrets import DeviceSecret, SecretsFile

SECRETS_TOML = """
[devices.thermostat-kitchen]
address = '192.0.2.10'
device_id = 'kitchen-device-id'
local_key = 'kitchen-local-key'

[devices.thermostat-hallway]
address = '192.0.2.11'
device_id = 'hallway-device-id'
local_key = 'hallway-local-key'
protocol = '3.4'

[panel]
password = 'panel-secret'
"""


@pytest.fixture
def f_secrets(tmp_path: Path) -> SecretsFile:
    path = tmp_path / 'secrets.toml'
    path.write_text(SECRETS_TOML, encoding='utf-8')
    return SecretsFile.init(path)


@pytest.fixture
def f_empty_secrets(tmp_path: Path) -> SecretsFile:
    return SecretsFile.init(tmp_path / 'absent.toml')


def test_device_secret_defaults_protocol(f_secrets: SecretsFile) -> None:
    assert f_secrets.device('thermostat-kitchen') == DeviceSecret(
        address='192.0.2.10',
        device_id='kitchen-device-id',
        local_key='kitchen-local-key',
        protocol='3.3',
    )


def test_device_secret_keeps_explicit_protocol(f_secrets: SecretsFile) -> None:
    assert f_secrets.device('thermostat-hallway').protocol == '3.4'


def test_panel_password_is_read(f_secrets: SecretsFile) -> None:
    assert f_secrets.panel_password == 'panel-secret'


def test_missing_file_has_no_secrets(f_empty_secrets: SecretsFile) -> None:
    with pytest.raises(SecretMissingError):
        f_empty_secrets.device('thermostat-kitchen')
```

`app/apps/devices/repositories/__init__.py`: empty file.

`app/apps/devices/repositories/device_tests.py`:

```python
import pytest

from apps.devices.domain.errors import DeviceNotFoundError
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.devices.repositories.device import DeviceRepository

pytestmark = pytest.mark.django_db


@pytest.fixture
def f_devices() -> list[Device]:
    return [
        DeviceFactory(slug='thermostat-kitchen'),
        DeviceFactory(slug='thermostat-hallway'),
        DeviceFactory(slug='plug-boiler', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG),
    ]


def test_thermostat_slugs_lists_only_thermostats_in_order(f_devices: list[Device]) -> None:
    assert DeviceRepository.thermostat_slugs() == [f_devices[1].slug, f_devices[0].slug]


def test_get_returns_device_by_slug(f_devices: list[Device]) -> None:
    assert DeviceRepository.get('plug-boiler') == f_devices[2]


def test_get_raises_for_unknown_slug() -> None:
    with pytest.raises(DeviceNotFoundError):
        DeviceRepository.get('thermostat-attic')
```

- [ ] **Step 2: Write the implementation**

`app/apps/core/services/secrets.py`:

```python
import tomllib
from dataclasses import dataclass
from pathlib import Path

from apps.core.domain.errors import SecretMissingError


@dataclass(frozen=True, kw_only=True)
class DeviceSecret:
    address: str
    device_id: str
    local_key: str
    protocol: str


class SecretsFile:
    def __init__(self, devices: dict[str, DeviceSecret], panel_password: str | None) -> None:
        self._devices = devices
        self._panel_password = panel_password

    @classmethod
    def init(cls, path: Path) -> 'SecretsFile':
        data = tomllib.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        devices = {
            slug: DeviceSecret(
                address=str(entry['address']),
                device_id=str(entry['device_id']),
                local_key=str(entry['local_key']),
                protocol=str(entry.get('protocol', '3.3')),
            )
            for slug, entry in data.get('devices', {}).items()
        }
        return cls(devices=devices, panel_password=data.get('panel', {}).get('password'))

    @property
    def panel_password(self) -> str | None:
        return self._panel_password

    def device(self, slug: str) -> DeviceSecret:
        if slug not in self._devices:
            raise SecretMissingError(slug)

        return self._devices[slug]
```

`app/apps/devices/models/device.py`:

```python
from django.db import models

from apps.devices.domain.kinds import DeviceDriver, DeviceKind


class Device(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=32, choices=[(kind.value, kind.value) for kind in DeviceKind])
    driver = models.CharField(max_length=32, choices=[(driver.value, driver.value) for driver in DeviceDriver])
    monitor_only = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('slug',)

    def __str__(self) -> str:
        return self.name
```

`app/apps/devices/models/__init__.py`:

```python
from apps.devices.models.device import Device

__all__ = ['Device']
```

`app/apps/devices/repositories/device.py`:

```python
from apps.devices.domain.errors import DeviceNotFoundError
from apps.devices.domain.kinds import DeviceKind
from apps.devices.models import Device


class DeviceRepository:
    @staticmethod
    def get(slug: str) -> Device:
        device = Device.objects.filter(slug=slug).first()
        if device is None:
            raise DeviceNotFoundError(slug)

        return device

    @staticmethod
    def thermostat_slugs() -> list[str]:
        return list(Device.objects.filter(kind=DeviceKind.THERMOSTAT).order_by('slug').values_list('slug', flat=True))
```

`app/apps/devices/admin.py`:

```python
from django.contrib import admin

from apps.devices.models import Device


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name', 'kind', 'driver', 'monitor_only')
    list_filter = ('kind', 'driver')
    search_fields = ('slug', 'name')
```

`app/apps/devices/factories.py`:

```python
import factory

from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.models import Device


class DeviceFactory(factory.django.DjangoModelFactory):
    slug = factory.Sequence(lambda n: f'device-{n}')
    name = factory.Sequence(lambda n: f'Device {n}')
    kind = DeviceKind.THERMOSTAT
    driver = DeviceDriver.TUYA_THERMOSTAT

    class Meta:
        model = Device
```

- [ ] **Step 3: Generate the migration**

```bash
docker compose run --rm --no-deps app python manage.py makemigrations devices
```

Expected: `Migrations for 'devices': apps/devices/migrations/0001_initial.py + Create model Device`. Create `app/apps/devices/migrations/__init__.py` if the command did not.

- [ ] **Step 4: Lint, commit, push, watch CI**

```bash
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/apps/core/services app/apps/devices
git commit -m 'Add device model, repository and secrets file'
git push origin stage-1
sh scripts/ci-watch.sh
```

```json:metadata
{"files": ["app/apps/core/services/secrets.py", "app/apps/core/services/secrets_tests.py", "app/apps/devices/models/device.py", "app/apps/devices/migrations/0001_initial.py", "app/apps/devices/repositories/device.py", "app/apps/devices/repositories/device_tests.py", "app/apps/devices/admin.py", "app/apps/devices/factories.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["SecretsFile reads device secrets and panel password; missing file yields no secrets; unknown slug raises SecretMissingError", "thermostat_slugs returns only thermostats ordered by slug; get raises DeviceNotFoundError", "makemigrations --check passes; CI green"], "modelTier": "standard"}
```

---

### Task 6: Tuya thermostat driver, driver registry and device stubs

**Goal:** `TuyaThermostatDriver` mapping ET-44W data points to `ThermostatState` and commands, a `DriverRegistry` that builds one driver per slug and raises `DriverNotImplementedError` for unsupported drivers, and reusable test stubs.

**Files:**
- Create: `app/apps/devices/drivers/__init__.py`, `app/apps/devices/drivers/tuya_thermostat.py`, `app/apps/devices/drivers/tuya_thermostat_tests.py`
- Create: `app/apps/devices/services/__init__.py`, `app/apps/devices/services/driver_registry.py`, `app/apps/devices/services/driver_registry_tests.py`
- Create: `app/apps/devices/stubs.py`

**Acceptance Criteria:**
- [ ] DPs `{'1': True, '2': 'hold', '16': 305, '24': 224}` read as power True, mode hold, setpoint 30.5, floor 22.4.
- [ ] `set_setpoint(Decimal('28.0'))`, `set_power(True)`, `set_mode(HOLD)` write `(16, 280)`, `(1, True)`, `(2, 'hold')`; a `None` write response counts as success.
- [ ] Unknown mode raises `DeviceProtocolError`; a Tuya `Error` payload or an `OSError` raises `DeviceUnavailableError`.
- [ ] The registry returns the same driver instance per slug, raises `DriverNotImplementedError` for `unsupported`, and `SecretMissingError` when secrets are missing.
- [ ] CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Write the stubs module**

`app/apps/devices/stubs.py`:

```python
from decimal import Decimal

from apps.core.domain.errors import DomainError
from apps.devices.domain.capabilities import FloorThermostat
from apps.devices.domain.errors import DeviceUnavailableError, DriverNotImplementedError
from apps.devices.domain.readings import ThermostatMode, ThermostatState


class FloorThermostatStub:
    def __init__(self, state: ThermostatState | None, error: DomainError | None = None) -> None:
        self._state = state
        self._error = error
        self._commands: list[tuple[str, object]] = []

    @property
    def commands(self) -> list[tuple[str, object]]:
        return self._commands

    def read_state(self) -> ThermostatState:
        if self._error is not None:
            raise self._error

        if self._state is None:
            raise DeviceUnavailableError('no state configured')

        return self._state

    def set_power(self, power: bool) -> None:
        self._command('power', power)

    def set_mode(self, mode: ThermostatMode) -> None:
        self._command('mode', mode)

    def set_setpoint(self, setpoint: Decimal) -> None:
        self._command('setpoint', setpoint)

    def _command(self, name: str, value: object) -> None:
        if self._error is not None:
            raise self._error

        self._commands.append((name, value))


class ThermostatProviderStub:
    def __init__(self, thermostats: dict[str, FloorThermostat]) -> None:
        self._thermostats = thermostats

    def thermostat(self, slug: str) -> FloorThermostat:
        if slug not in self._thermostats:
            raise DriverNotImplementedError(slug)

        return self._thermostats[slug]
```

- [ ] **Step 2: Write the driver tests**

`app/apps/devices/drivers/__init__.py` and `app/apps/devices/services/__init__.py`: empty files.

`app/apps/devices/drivers/tuya_thermostat_tests.py`:

```python
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.devices.domain.errors import DeviceProtocolError, DeviceUnavailableError
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.drivers.tuya_thermostat import TuyaPayload, TuyaThermostatDriver

NOW = datetime(2026, 10, 29, 5, 0, tzinfo=UTC)
KITCHEN_DPS = {'1': True, '2': 'hold', '16': 305, '24': 224, '104': 0}


class TuyaClientStub:
    def __init__(self, status_payload: TuyaPayload | None, error: OSError | None = None) -> None:
        self._status_payload = status_payload
        self._error = error
        self._writes: list[tuple[int, bool | int | str]] = []

    @property
    def writes(self) -> list[tuple[int, bool | int | str]]:
        return self._writes

    def status(self) -> TuyaPayload | None:
        if self._error is not None:
            raise self._error

        return self._status_payload

    def set_value(self, index: int, value: bool | int | str) -> TuyaPayload | None:
        if self._error is not None:
            raise self._error

        self._writes.append((index, value))
        return None


def _now() -> datetime:
    return NOW


@pytest.fixture
def f_client() -> TuyaClientStub:
    return TuyaClientStub(status_payload={'dps': KITCHEN_DPS})


@pytest.fixture
def f_driver(f_client: TuyaClientStub) -> TuyaThermostatDriver:
    return TuyaThermostatDriver(client=f_client, clock=_now)


@pytest.fixture
def f_unknown_mode_driver() -> TuyaThermostatDriver:
    return TuyaThermostatDriver(client=TuyaClientStub(status_payload={'dps': {**KITCHEN_DPS, '2': 'eco'}}), clock=_now)


@pytest.fixture
def f_error_payload_driver() -> TuyaThermostatDriver:
    client = TuyaClientStub(status_payload={'Error': 'Network Error: Unable to Connect', 'Err': '901'})
    return TuyaThermostatDriver(client=client, clock=_now)


@pytest.fixture
def f_offline_driver() -> TuyaThermostatDriver:
    client = TuyaClientStub(status_payload=None, error=ConnectionRefusedError('connection refused'))
    return TuyaThermostatDriver(client=client, clock=_now)


def test_read_state_maps_data_points(f_driver: TuyaThermostatDriver) -> None:
    assert f_driver.read_state() == ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('30.5'),
        floor_temperature=Decimal('22.4'),
        read_at=NOW,
    )


def test_commands_write_data_points(f_driver: TuyaThermostatDriver, f_client: TuyaClientStub) -> None:
    f_driver.set_setpoint(Decimal('28.0'))
    f_driver.set_power(True)
    f_driver.set_mode(ThermostatMode.HOLD)

    assert f_client.writes == [(16, 280), (1, True), (2, 'hold')]


def test_read_state_rejects_unknown_mode(f_unknown_mode_driver: TuyaThermostatDriver) -> None:
    with pytest.raises(DeviceProtocolError):
        f_unknown_mode_driver.read_state()


def test_read_state_reports_tuya_error(f_error_payload_driver: TuyaThermostatDriver) -> None:
    with pytest.raises(DeviceUnavailableError, match='Unable to Connect'):
        f_error_payload_driver.read_state()


def test_commands_report_socket_errors(f_offline_driver: TuyaThermostatDriver) -> None:
    with pytest.raises(DeviceUnavailableError, match='connection refused'):
        f_offline_driver.set_power(False)
```

- [ ] **Step 3: Write the driver**

`app/apps/devices/drivers/tuya_thermostat.py`:

```python
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

import tinytuya
from django.utils import timezone

from apps.core.services.secrets import DeviceSecret
from apps.devices.domain.errors import DeviceProtocolError, DeviceUnavailableError
from apps.devices.domain.readings import ThermostatMode, ThermostatState

POWER_DP = 1
MODE_DP = 2
SETPOINT_DP = 16
FLOOR_TEMPERATURE_DP = 24
TENTHS = Decimal(10)

TuyaPayload = dict[str, object]


class TuyaClient(Protocol):
    def status(self) -> TuyaPayload | None: ...

    def set_value(self, index: int, value: bool | int | str) -> TuyaPayload | None: ...


class TuyaThermostatDriver:
    def __init__(self, client: TuyaClient, clock: Callable[[], datetime]) -> None:
        self._client = client
        self._clock = clock

    @classmethod
    def init(cls, secret: DeviceSecret) -> 'TuyaThermostatDriver':
        client = tinytuya.Device(
            secret.device_id,
            secret.address,
            secret.local_key,
            version=float(secret.protocol),
            connection_timeout=5,
            connection_retry_limit=2,
            connection_retry_delay=1,
        )
        return cls(client=client, clock=timezone.now)

    def read_state(self) -> ThermostatState:
        try:
            payload = self._client.status()
        except OSError as error:
            raise DeviceUnavailableError(str(error)) from error

        if not payload or 'Error' in payload:
            raise DeviceUnavailableError(str((payload or {}).get('Error', 'empty response')))

        return self._state(payload)

    def set_power(self, power: bool) -> None:
        self._write(POWER_DP, power)

    def set_mode(self, mode: ThermostatMode) -> None:
        self._write(MODE_DP, mode.value)

    def set_setpoint(self, setpoint: Decimal) -> None:
        self._write(SETPOINT_DP, int(setpoint * TENTHS))

    def _state(self, payload: TuyaPayload) -> ThermostatState:
        dps = payload.get('dps')
        if not isinstance(dps, dict):
            raise DeviceProtocolError(f'payload without dps: {payload}')

        try:
            return ThermostatState(
                power=bool(dps[str(POWER_DP)]),
                mode=ThermostatMode(str(dps[str(MODE_DP)])),
                setpoint=Decimal(str(dps[str(SETPOINT_DP)])) / TENTHS,
                floor_temperature=Decimal(str(dps[str(FLOOR_TEMPERATURE_DP)])) / TENTHS,
                read_at=self._clock(),
            )
        except (KeyError, ValueError, InvalidOperation) as error:
            raise DeviceProtocolError(f'unexpected thermostat payload: {dps}') from error

    def _write(self, dp: int, value: bool | int | str) -> None:
        try:
            payload = self._client.set_value(dp, value)
        except OSError as error:
            raise DeviceUnavailableError(str(error)) from error

        if payload and 'Error' in payload:
            raise DeviceUnavailableError(str(payload['Error']))
```

- [ ] **Step 4: Write the registry tests**

`app/apps/devices/services/driver_registry_tests.py`:

```python
import pytest

from apps.core.domain.errors import SecretMissingError
from apps.core.services.secrets import DeviceSecret, SecretsFile
from apps.devices.domain.capabilities import FloorThermostat
from apps.devices.domain.errors import DriverNotImplementedError
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.driver_registry import DriverRegistry
from apps.devices.stubs import FloorThermostatStub

pytestmark = pytest.mark.django_db

SECRET = DeviceSecret(address='192.0.2.10', device_id='device-id', local_key='local-key', protocol='3.3')


def _stub_factory(_secret: DeviceSecret) -> FloorThermostat:
    return FloorThermostatStub(state=None)


@pytest.fixture
def f_registry() -> DriverRegistry:
    DeviceFactory(slug='thermostat-kitchen')
    DeviceFactory(slug='thermostat-without-secret')
    DeviceFactory(slug='climate-bedroom', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.UNSUPPORTED)
    secrets = SecretsFile(devices={'thermostat-kitchen': SECRET, 'climate-bedroom': SECRET}, panel_password=None)
    return DriverRegistry(
        devices=DeviceRepository(),
        secrets=secrets,
        thermostat_factories={DeviceDriver.TUYA_THERMOSTAT: _stub_factory},
    )


def test_thermostat_is_built_once_per_slug(f_registry: DriverRegistry) -> None:
    first = f_registry.thermostat('thermostat-kitchen')
    second = f_registry.thermostat('thermostat-kitchen')

    assert first is second


def test_unsupported_driver_raises(f_registry: DriverRegistry) -> None:
    with pytest.raises(DriverNotImplementedError):
        f_registry.thermostat('climate-bedroom')


def test_missing_secret_raises(f_registry: DriverRegistry) -> None:
    with pytest.raises(SecretMissingError):
        f_registry.thermostat('thermostat-without-secret')
```

- [ ] **Step 5: Write the registry**

`app/apps/devices/services/driver_registry.py`:

```python
from collections.abc import Callable
from pathlib import Path

from django.conf import settings

from apps.core.services.secrets import DeviceSecret, SecretsFile
from apps.devices.domain.capabilities import FloorThermostat
from apps.devices.domain.errors import DriverNotImplementedError
from apps.devices.domain.kinds import DeviceDriver
from apps.devices.drivers.tuya_thermostat import TuyaThermostatDriver
from apps.devices.repositories.device import DeviceRepository

ThermostatFactory = Callable[[DeviceSecret], FloorThermostat]


class DriverRegistry:
    def __init__(
        self,
        devices: DeviceRepository,
        secrets: SecretsFile,
        thermostat_factories: dict[DeviceDriver, ThermostatFactory],
    ) -> None:
        self._devices = devices
        self._secrets = secrets
        self._thermostat_factories = thermostat_factories
        self._thermostats: dict[str, FloorThermostat] = {}

    @classmethod
    def init(cls) -> 'DriverRegistry':
        return cls(
            devices=DeviceRepository(),
            secrets=SecretsFile.init(Path(settings.TEPLO_SECRETS_FILE)),
            thermostat_factories={DeviceDriver.TUYA_THERMOSTAT: TuyaThermostatDriver.init},
        )

    def thermostat(self, slug: str) -> FloorThermostat:
        if slug not in self._thermostats:
            self._thermostats[slug] = self._build_thermostat(slug)

        return self._thermostats[slug]

    def _build_thermostat(self, slug: str) -> FloorThermostat:
        device = self._devices.get(slug)
        factory = self._thermostat_factories.get(DeviceDriver(device.driver))
        if factory is None:
            raise DriverNotImplementedError(device.driver)

        return factory(self._secrets.device(slug))


driver_registry = DriverRegistry.init()
```

- [ ] **Step 6: Lint, commit, push, watch CI**

```bash
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/apps/devices
git commit -m 'Add Tuya thermostat driver and driver registry'
git push origin stage-1
sh scripts/ci-watch.sh
```

```json:metadata
{"files": ["app/apps/devices/stubs.py", "app/apps/devices/drivers/tuya_thermostat.py", "app/apps/devices/drivers/tuya_thermostat_tests.py", "app/apps/devices/services/driver_registry.py", "app/apps/devices/services/driver_registry_tests.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["DPs 1/2/16/24 map to power, mode, setpoint 30.5 and floor 22.4", "commands write (16, 280), (1, True), (2, 'hold'); None write response is success", "unknown mode raises DeviceProtocolError; Tuya Error payload or OSError raises DeviceUnavailableError", "registry caches per slug, raises DriverNotImplementedError for unsupported and SecretMissingError without secrets", "CI green"], "modelTier": "mechanical"}
```

---

### Task 7: Thermostat state store and polling use case

**Goal:** A thread-safe in-memory `ThermostatStateStore` with a 10-minute availability window and `PollThermostatsUseCase` that reads every thermostat, records states and failures, and emits `DeviceAvailabilityChanged` when availability flips.

**Files:**
- Create: `app/apps/devices/services/state_store.py`, `app/apps/devices/services/state_store_tests.py`
- Create: `app/apps/devices/usecases/__init__.py`, `app/apps/devices/usecases/poll_thermostats.py`, `app/apps/devices/usecases/poll_thermostats_tests.py`

**Acceptance Criteria:**
- [ ] A state read 10 minutes ago is available; 11 minutes ago is unavailable; a failure keeps the last state and records the error; a success clears it.
- [ ] One failing thermostat does not stop polling the others; output lists polled and failed slugs.
- [ ] The first poll reports each device's availability once; a device whose last reading is older than 10 minutes and whose poll fails is reported unavailable with the failure reason.
- [ ] CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Write the state store tests**

`app/apps/devices/services/state_store_tests.py`:

```python
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.services.state_store import ThermostatSnapshot, ThermostatStateStore

NOW = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)


@pytest.fixture
def f_state() -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('22.0'),
        floor_temperature=Decimal('21.5'),
        read_at=NOW - timedelta(minutes=10),
    )


@pytest.fixture
def f_store(f_state: ThermostatState) -> ThermostatStateStore:
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_state('thermostat-kitchen', f_state)
    store.record_failure('thermostat-kitchen', 'timeout')
    return store


@pytest.fixture
def f_recovered_store(f_store: ThermostatStateStore, f_state: ThermostatState) -> ThermostatStateStore:
    f_store.record_state('thermostat-kitchen', replace(f_state, read_at=NOW))
    return f_store


def test_failure_keeps_last_state_and_error(f_store: ThermostatStateStore, f_state: ThermostatState) -> None:
    assert f_store.snapshot('thermostat-kitchen', NOW) == ThermostatSnapshot(
        slug='thermostat-kitchen',
        state=f_state,
        available=True,
        error='timeout',
    )


def test_state_older_than_window_is_unavailable(f_store: ThermostatStateStore) -> None:
    assert f_store.snapshot('thermostat-kitchen', NOW + timedelta(minutes=1)).available is False


def test_success_clears_error(f_recovered_store: ThermostatStateStore) -> None:
    assert f_recovered_store.snapshot('thermostat-kitchen', NOW).error is None


def test_unknown_device_is_unavailable(f_store: ThermostatStateStore) -> None:
    assert f_store.snapshot('thermostat-attic', NOW) == ThermostatSnapshot(
        slug='thermostat-attic',
        state=None,
        available=False,
        error=None,
    )
```

- [ ] **Step 2: Write the state store**

`app/apps/devices/services/state_store.py`:

```python
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.devices.domain.readings import ThermostatState

UNAVAILABLE_AFTER = timedelta(minutes=10)


@dataclass(frozen=True, kw_only=True)
class ThermostatSnapshot:
    slug: str
    state: ThermostatState | None
    available: bool
    error: str | None


class ThermostatStateStore:
    def __init__(self, unavailable_after: timedelta) -> None:
        self._unavailable_after = unavailable_after
        self._states: dict[str, ThermostatState] = {}
        self._errors: dict[str, str] = {}
        self._reported: dict[str, bool] = {}
        self._lock = threading.Lock()

    def record_state(self, slug: str, state: ThermostatState) -> None:
        with self._lock:
            self._states[slug] = state
            self._errors.pop(slug, None)

    def record_failure(self, slug: str, error: str) -> None:
        with self._lock:
            self._errors[slug] = error

    def snapshot(self, slug: str, now: datetime) -> ThermostatSnapshot:
        with self._lock:
            state = self._states.get(slug)
            error = self._errors.get(slug)
        available = state is not None and now - state.read_at <= self._unavailable_after
        return ThermostatSnapshot(slug=slug, state=state, available=available, error=error)

    def last_reported(self, slug: str) -> bool | None:
        with self._lock:
            return self._reported.get(slug)

    def mark_reported(self, slug: str, available: bool) -> None:
        with self._lock:
            self._reported[slug] = available


thermostat_state_store = ThermostatStateStore(unavailable_after=UNAVAILABLE_AFTER)
```

- [ ] **Step 3: Write the use case tests**

`app/apps/devices/usecases/__init__.py`: empty file.

`app/apps/devices/usecases/poll_thermostats_tests.py`:

```python
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import ThermostatStateStore
from apps.devices.stubs import FloorThermostatStub, ThermostatProviderStub
from apps.devices.usecases.poll_thermostats import (
    PollThermostatsInputDTO,
    PollThermostatsOutputDTO,
    PollThermostatsUseCase,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)


@pytest.fixture
def f_state() -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('22.0'),
        floor_temperature=Decimal('21.5'),
        read_at=NOW,
    )


@pytest.fixture
def f_store() -> ThermostatStateStore:
    return ThermostatStateStore(unavailable_after=timedelta(minutes=10))


@pytest.fixture
def f_stale_store(f_state: ThermostatState) -> ThermostatStateStore:
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_state('thermostat-hallway', replace(f_state, read_at=NOW - timedelta(minutes=11)))
    store.mark_reported('thermostat-hallway', True)
    store.record_state('thermostat-kitchen', f_state)
    store.mark_reported('thermostat-kitchen', True)
    return store


@pytest.fixture
def f_received() -> list[DomainEvent]:
    return []


@pytest.fixture
def provider(f_state: ThermostatState) -> ThermostatProviderStub:
    DeviceFactory(slug='thermostat-kitchen')
    DeviceFactory(slug='thermostat-hallway')
    return ThermostatProviderStub(
        thermostats={
            'thermostat-kitchen': FloorThermostatStub(state=f_state),
            'thermostat-hallway': FloorThermostatStub(state=None, error=DeviceUnavailableError('timeout')),
        },
    )


@pytest.fixture
def dispatcher(f_received: list[DomainEvent]) -> EventDispatcher:
    event_dispatcher = EventDispatcher()
    event_dispatcher.register(DeviceAvailabilityChanged, f_received.append)
    return event_dispatcher


@pytest.fixture
def f_use_case(
    provider: ThermostatProviderStub,
    f_store: ThermostatStateStore,
    dispatcher: EventDispatcher,
) -> PollThermostatsUseCase:
    return PollThermostatsUseCase(devices=DeviceRepository(), provider=provider, store=f_store, dispatcher=dispatcher)


@pytest.fixture
def f_stale_use_case(
    provider: ThermostatProviderStub,
    f_stale_store: ThermostatStateStore,
    dispatcher: EventDispatcher,
) -> PollThermostatsUseCase:
    return PollThermostatsUseCase(
        devices=DeviceRepository(),
        provider=provider,
        store=f_stale_store,
        dispatcher=dispatcher,
    )


def test_poll_records_states_and_failures(f_use_case: PollThermostatsUseCase, f_store: ThermostatStateStore) -> None:
    output = f_use_case.execute(PollThermostatsInputDTO(now=NOW))

    assert (output, f_store.snapshot('thermostat-hallway', NOW).error) == (
        PollThermostatsOutputDTO(polled=('thermostat-kitchen',), failed=('thermostat-hallway',)),
        'timeout',
    )


def test_first_poll_reports_availability_once(
    f_use_case: PollThermostatsUseCase,
    f_received: list[DomainEvent],
) -> None:
    f_use_case.execute(PollThermostatsInputDTO(now=NOW))
    f_use_case.execute(PollThermostatsInputDTO(now=NOW))

    assert f_received == [
        DeviceAvailabilityChanged(slug='thermostat-hallway', available=False, reason='timeout', occurred_at=NOW),
        DeviceAvailabilityChanged(
            slug='thermostat-kitchen',
            available=True,
            reason='reading received',
            occurred_at=NOW,
        ),
    ]


def test_stale_failing_device_is_reported_unavailable(
    f_stale_use_case: PollThermostatsUseCase,
    f_received: list[DomainEvent],
) -> None:
    f_stale_use_case.execute(PollThermostatsInputDTO(now=NOW))

    assert f_received == [
        DeviceAvailabilityChanged(slug='thermostat-hallway', available=False, reason='timeout', occurred_at=NOW),
    ]
```

- [ ] **Step 4: Write the use case**

`app/apps/devices/usecases/poll_thermostats.py`:

```python
from dataclasses import dataclass
from datetime import datetime

import structlog

from apps.core.domain.errors import DomainError
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.capabilities import ThermostatProvider
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.driver_registry import driver_registry
from apps.devices.services.state_store import ThermostatStateStore, thermostat_state_store

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class PollThermostatsInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class PollThermostatsOutputDTO:
    polled: tuple[str, ...]
    failed: tuple[str, ...]


class PollThermostatsUseCase(BaseUseCase[PollThermostatsInputDTO, PollThermostatsOutputDTO]):
    def __init__(
        self,
        devices: DeviceRepository,
        provider: ThermostatProvider,
        store: ThermostatStateStore,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._devices = devices
        self._provider = provider
        self._store = store

    @classmethod
    def init(cls) -> 'PollThermostatsUseCase':
        return cls(devices=DeviceRepository(), provider=driver_registry, store=thermostat_state_store)

    def _execute(self, input_dto: PollThermostatsInputDTO) -> PollThermostatsOutputDTO:
        polled: list[str] = []
        failed: list[str] = []
        for slug in self._devices.thermostat_slugs():
            (polled if self._poll(slug) else failed).append(slug)
            self._report_availability(slug, input_dto.now)
        return PollThermostatsOutputDTO(polled=tuple(polled), failed=tuple(failed))

    def _poll(self, slug: str) -> bool:
        try:
            state = self._provider.thermostat(slug).read_state()
        except DomainError as error:
            self._store.record_failure(slug, error.detail)
            logger.warning(
                'thermostat_poll_failed',
                device=slug,
                error_code=error.error_code,
                detail=error.detail,
            )
            return False

        self._store.record_state(slug, state)
        return True

    def _report_availability(self, slug: str, now: datetime) -> None:
        snapshot = self._store.snapshot(slug, now)
        if self._store.last_reported(slug) == snapshot.available:
            return

        self._store.mark_reported(slug, snapshot.available)
        self._event_store.add_event(
            DeviceAvailabilityChanged(
                slug=slug,
                available=snapshot.available,
                reason='reading received' if snapshot.available else snapshot.error or 'no reading',
                occurred_at=now,
            ),
        )
```

- [ ] **Step 5: Lint, commit, push, watch CI**

```bash
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/apps/devices
git commit -m 'Add thermostat state store and polling use case'
git push origin stage-1
sh scripts/ci-watch.sh
```

```json:metadata
{"files": ["app/apps/devices/services/state_store.py", "app/apps/devices/services/state_store_tests.py", "app/apps/devices/usecases/poll_thermostats.py", "app/apps/devices/usecases/poll_thermostats_tests.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["10-minute availability window; failure keeps last state and error; success clears error", "one failing thermostat does not stop the others; output lists polled and failed", "first poll reports availability once; stale failing device reported unavailable with reason", "CI green"], "modelTier": "mechanical"}
```

---

### Task 8: Clock trust

**Goal:** `ClockStatus` and the `ClockTrustSource` port, an NDMS source that trusts the clock only when `ndmc -c 'show ntp status'` reports `synchronized: yes`, a system source for development and CI, and a settings-driven factory.

**Files:**
- Create: `app/apps/system/domain/__init__.py`, `app/apps/system/domain/clock.py`
- Create: `app/apps/system/services/__init__.py`, `app/apps/system/services/clock_trust.py`, `app/apps/system/services/clock_trust_tests.py`
- Create: `app/apps/system/stubs.py`

**Acceptance Criteria:**
- [ ] NDMS output containing `synchronized: yes` is trusted; `synchronized: no`, missing status or a failing `ndmc` are untrusted with a reason.
- [ ] `clock_trust_source()` returns `NdmsNtpSource` when `TEPLO_CLOCK_SOURCE='ndms'`, otherwise `SystemClockSource`.
- [ ] CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Write the domain and the stub**

`app/apps/system/domain/__init__.py` and `app/apps/system/services/__init__.py`: empty files.

`app/apps/system/domain/clock.py`:

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, kw_only=True)
class ClockStatus:
    trusted: bool
    reason: str


class ClockTrustSource(Protocol):
    def status(self) -> ClockStatus: ...
```

`app/apps/system/stubs.py`:

```python
from apps.system.domain.clock import ClockStatus


class ClockStub:
    def __init__(self, trusted: bool) -> None:
        self._trusted = trusted

    def status(self) -> ClockStatus:
        return ClockStatus(trusted=self._trusted, reason='stub')
```

- [ ] **Step 2: Write the tests**

`app/apps/system/services/clock_trust_tests.py`:

```python
import pytest
from pytest_django.fixtures import SettingsWrapper

from apps.system.domain.clock import ClockStatus
from apps.system.services.clock_trust import NdmsNtpSource, SystemClockSource, clock_trust_source

SYNCHRONIZED_OUTPUT = (
    '\x1b[K\n           status: \n              elapsed: 74945\n               server: 1.ntp.netcraze.ru\n'
    '             accurate: yes\n         synchronized: yes\n'
)


def _synchronized(_command: tuple[str, ...]) -> str:
    return SYNCHRONIZED_OUTPUT


def _unsynchronized(_command: tuple[str, ...]) -> str:
    return SYNCHRONIZED_OUTPUT.replace('synchronized: yes', 'synchronized: no')


def _silent(_command: tuple[str, ...]) -> str:
    return ''


def _missing_ndmc(_command: tuple[str, ...]) -> str:
    raise FileNotFoundError('/bin/ndmc')


@pytest.fixture
def f_synchronized_source() -> NdmsNtpSource:
    return NdmsNtpSource(run=_synchronized)


@pytest.fixture
def f_unsynchronized_source() -> NdmsNtpSource:
    return NdmsNtpSource(run=_unsynchronized)


@pytest.fixture
def f_silent_source() -> NdmsNtpSource:
    return NdmsNtpSource(run=_silent)


@pytest.fixture
def f_missing_ndmc_source() -> NdmsNtpSource:
    return NdmsNtpSource(run=_missing_ndmc)


@pytest.fixture
def ndms_settings(settings: SettingsWrapper) -> SettingsWrapper:
    settings.TEPLO_CLOCK_SOURCE = 'ndms'
    return settings


def test_synchronized_ntp_is_trusted(f_synchronized_source: NdmsNtpSource) -> None:
    assert f_synchronized_source.status() == ClockStatus(trusted=True, reason='ntp synchronized')


def test_unsynchronized_ntp_is_untrusted(f_unsynchronized_source: NdmsNtpSource) -> None:
    assert f_unsynchronized_source.status() == ClockStatus(trusted=False, reason='ntp not synchronized')


def test_missing_status_is_untrusted(f_silent_source: NdmsNtpSource) -> None:
    assert f_silent_source.status() == ClockStatus(trusted=False, reason='ntp status missing')


def test_failing_ndmc_is_untrusted(f_missing_ndmc_source: NdmsNtpSource) -> None:
    assert f_missing_ndmc_source.status() == ClockStatus(trusted=False, reason='ndmc failed: /bin/ndmc')


def test_factory_uses_system_source_in_tests() -> None:
    assert isinstance(clock_trust_source(), SystemClockSource)


@pytest.mark.usefixtures('ndms_settings')
def test_factory_uses_ndms_source_on_router() -> None:
    assert isinstance(clock_trust_source(), NdmsNtpSource)
```

- [ ] **Step 3: Write the sources**

`app/apps/system/services/clock_trust.py`:

```python
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
```

- [ ] **Step 4: Lint, commit, push, watch CI**

```bash
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/apps/system
git commit -m 'Add clock trust from NDMS NTP status'
git push origin stage-1
sh scripts/ci-watch.sh
```

```json:metadata
{"files": ["app/apps/system/domain/clock.py", "app/apps/system/services/clock_trust.py", "app/apps/system/services/clock_trust_tests.py", "app/apps/system/stubs.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["synchronized: yes is trusted; synchronized: no, missing status or failing ndmc untrusted with reason", "clock_trust_source returns NdmsNtpSource for ndms, SystemClockSource otherwise", "CI green"], "modelTier": "mechanical"}
```

---

### Task 9: Climate domain — schedule, floor policy, change planning, write guard

**Goal:** Pure policies: resolve comfort / pre-heat / night for a moment, pick the floor target (comfort, night, boost, untrusted clock, disabled zone), plan the minimal thermostat change and enforce the 30-minute write budget.

**Files:**
- Create: `app/apps/climate/domain/__init__.py`, `modes.py`, `zone.py`, `schedule.py`, `schedule_tests.py`, `floor_policy.py`, `floor_policy_tests.py`, `change.py`, `change_tests.py`, `write_guard.py`, `write_guard_tests.py`, `events.py`, `errors.py` (all under `app/apps/climate/domain/`)

**Acceptance Criteria:**
- [ ] With daily comfort 06:30–23:30 and a 3-hour lead: 06:30 and 12:00 → comfort, 23:30, 01:00 and 03:29 → night, 03:30 and 06:29 → pre-heat.
- [ ] A Friday 01:00 window puts Thursday 22:00 in pre-heat and Thursday 02:00 in night.
- [ ] Night → night setpoint; pre-heat, comfort or untrusted clock → comfort setpoint; active boost → boost setpoint; expired boost ignored; disabled zone → power off.
- [ ] `plan_change` is empty when the state matches, changes only the setpoint when needed, switches `smart` to `hold` and powers on, and powers off only a powered thermostat.
- [ ] `write_allowed` is False 29 minutes after the last command and True at 30 minutes or with no previous command.
- [ ] Architecture tests pass; CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Write the value objects, events and errors**

`app/apps/climate/domain/__init__.py`: empty file.

`app/apps/climate/domain/modes.py`:

```python
from enum import StrEnum


class ZoneKind(StrEnum):
    AIR = 'air'
    FLOOR = 'floor'


class ScheduleMode(StrEnum):
    COMFORT = 'comfort'
    PREHEAT = 'preheat'
    NIGHT = 'night'
```

`app/apps/climate/domain/zone.py`:

```python
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from apps.climate.domain.modes import ZoneKind


@dataclass(frozen=True, kw_only=True)
class FloorSetpoints:
    comfort: Decimal
    night: Decimal
    boost: Decimal


@dataclass(frozen=True, kw_only=True)
class ComfortWindow:
    weekday: int
    start: time
    end: time


@dataclass(frozen=True, kw_only=True)
class ZoneSettings:
    slug: str
    name: str
    kind: ZoneKind
    thermostat_slug: str
    heating_enabled: bool
    live_control: bool
    setpoints: FloorSetpoints
    preheat_lead: timedelta
    windows: tuple[ComfortWindow, ...]
    boost_until: datetime | None
    last_command_at: datetime | None

    def is_boosted(self, now: datetime) -> bool:
        return self.boost_until is not None and now < self.boost_until
```

`app/apps/climate/domain/change.py`:

```python
from dataclasses import dataclass
from decimal import Decimal

from apps.climate.domain.floor_policy import ThermostatTarget
from apps.devices.domain.readings import ThermostatMode, ThermostatState


@dataclass(frozen=True, kw_only=True)
class ThermostatChange:
    power: bool | None = None
    mode: ThermostatMode | None = None
    setpoint: Decimal | None = None

    @property
    def is_empty(self) -> bool:
        return self.power is None and self.mode is None and self.setpoint is None


def plan_change(target: ThermostatTarget, state: ThermostatState) -> ThermostatChange:
    if not target.power:
        return ThermostatChange(power=False) if state.power else ThermostatChange()

    return ThermostatChange(
        power=None if state.power else True,
        mode=None if state.mode is ThermostatMode.HOLD else ThermostatMode.HOLD,
        setpoint=None if target.setpoint is None or target.setpoint == state.setpoint else target.setpoint,
    )
```

`app/apps/climate/domain/events.py`:

```python
from dataclasses import dataclass
from datetime import datetime

from apps.climate.domain.change import ThermostatChange
from apps.core.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class ZoneCommandIssued(DomainEvent):
    zone: str
    change: ThermostatChange
    dry_run: bool


@dataclass(frozen=True, kw_only=True)
class ZoneCommandFailed(DomainEvent):
    zone: str
    change: ThermostatChange
    error: str


@dataclass(frozen=True, kw_only=True)
class ZoneBoosted(DomainEvent):
    zone: str
    until: datetime
```

`app/apps/climate/domain/errors.py`:

```python
from apps.core.domain.errors import DomainError


class ZoneNotFoundError(DomainError):
    def __init__(self, slug: str) -> None:
        super().__init__(f'zone not found: {slug}', error_code='zone_not_found')
```

- [ ] **Step 2: Write the policy tests**

`app/apps/climate/domain/schedule_tests.py`:

```python
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.schedule import resolve_mode
from apps.climate.domain.zone import ComfortWindow

MOSCOW = ZoneInfo('Europe/Moscow')
LEAD = timedelta(hours=3)


@pytest.fixture
def f_daily_windows() -> tuple[ComfortWindow, ...]:
    return tuple(ComfortWindow(weekday=weekday, start=time(6, 30), end=time(23, 30)) for weekday in range(7))


@pytest.fixture
def f_friday_early_window() -> tuple[ComfortWindow, ...]:
    return (ComfortWindow(weekday=4, start=time(1, 0), end=time(7, 0)),)


@pytest.mark.parametrize(
    ('moment', 'expected'),
    [
        (datetime(2026, 10, 29, 6, 30, tzinfo=MOSCOW), ScheduleMode.COMFORT),
        (datetime(2026, 10, 29, 12, 0, tzinfo=MOSCOW), ScheduleMode.COMFORT),
        (datetime(2026, 10, 29, 23, 30, tzinfo=MOSCOW), ScheduleMode.NIGHT),
        (datetime(2026, 10, 29, 1, 0, tzinfo=MOSCOW), ScheduleMode.NIGHT),
        (datetime(2026, 10, 29, 3, 29, tzinfo=MOSCOW), ScheduleMode.NIGHT),
        (datetime(2026, 10, 29, 3, 30, tzinfo=MOSCOW), ScheduleMode.PREHEAT),
        (datetime(2026, 10, 29, 6, 29, tzinfo=MOSCOW), ScheduleMode.PREHEAT),
    ],
)
def test_resolve_mode_for_daily_comfort(
    f_daily_windows: tuple[ComfortWindow, ...],
    moment: datetime,
    expected: ScheduleMode,
) -> None:
    assert resolve_mode(f_daily_windows, moment, LEAD) is expected


def test_preheat_starts_on_previous_day(f_friday_early_window: tuple[ComfortWindow, ...]) -> None:
    moment = datetime(2026, 10, 29, 22, 0, tzinfo=MOSCOW)

    assert resolve_mode(f_friday_early_window, moment, LEAD) is ScheduleMode.PREHEAT


def test_window_applies_only_on_its_weekday(f_friday_early_window: tuple[ComfortWindow, ...]) -> None:
    moment = datetime(2026, 10, 29, 2, 0, tzinfo=MOSCOW)

    assert resolve_mode(f_friday_early_window, moment, LEAD) is ScheduleMode.NIGHT
```

`app/apps/climate/domain/floor_policy_tests.py`:

```python
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.floor_policy import ThermostatTarget, decide_floor_target
from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.domain.zone import FloorSetpoints, ZoneSettings

NOW = datetime(2026, 10, 29, 2, 0, tzinfo=ZoneInfo('Europe/Moscow'))


@pytest.fixture
def f_zone() -> ZoneSettings:
    return ZoneSettings(
        slug='kitchen',
        name='Kitchen',
        kind=ZoneKind.AIR,
        thermostat_slug='thermostat-kitchen',
        heating_enabled=True,
        live_control=True,
        setpoints=FloorSetpoints(comfort=Decimal('28.0'), night=Decimal('22.0'), boost=Decimal('30.0')),
        preheat_lead=timedelta(hours=3),
        windows=(),
        boost_until=None,
        last_command_at=None,
    )


@pytest.fixture
def f_boosted_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, boost_until=NOW + timedelta(hours=1))


@pytest.fixture
def f_expired_boost_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, boost_until=NOW)


@pytest.fixture
def f_disabled_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, heating_enabled=False)


@pytest.mark.parametrize(
    ('mode', 'clock_trusted', 'expected'),
    [
        (ScheduleMode.NIGHT, True, Decimal('22.0')),
        (ScheduleMode.PREHEAT, True, Decimal('28.0')),
        (ScheduleMode.COMFORT, True, Decimal('28.0')),
        (ScheduleMode.NIGHT, False, Decimal('28.0')),
    ],
)
def test_floor_target_follows_schedule_mode(
    f_zone: ZoneSettings,
    mode: ScheduleMode,
    clock_trusted: bool,
    expected: Decimal,
) -> None:
    target = decide_floor_target(f_zone, mode, NOW, clock_trusted=clock_trusted)

    assert target == ThermostatTarget(power=True, setpoint=expected)


def test_boost_overrides_night(f_boosted_zone: ZoneSettings) -> None:
    target = decide_floor_target(f_boosted_zone, ScheduleMode.NIGHT, NOW, clock_trusted=True)

    assert target == ThermostatTarget(power=True, setpoint=Decimal('30.0'))


def test_expired_boost_is_ignored(f_expired_boost_zone: ZoneSettings) -> None:
    target = decide_floor_target(f_expired_boost_zone, ScheduleMode.NIGHT, NOW, clock_trusted=True)

    assert target == ThermostatTarget(power=True, setpoint=Decimal('22.0'))


def test_disabled_zone_turns_thermostat_off(f_disabled_zone: ZoneSettings) -> None:
    target = decide_floor_target(f_disabled_zone, ScheduleMode.COMFORT, NOW, clock_trusted=True)

    assert target == ThermostatTarget(power=False, setpoint=None)
```

`app/apps/climate/domain/change_tests.py`:

```python
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.climate.domain.change import ThermostatChange, plan_change
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.devices.domain.readings import ThermostatMode, ThermostatState

OFF = ThermostatTarget(power=False, setpoint=None)


@pytest.fixture
def f_hold_state() -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('22.0'),
        floor_temperature=Decimal('21.0'),
        read_at=datetime(2026, 10, 29, 2, 0, tzinfo=UTC),
    )


@pytest.fixture
def f_program_off_state(f_hold_state: ThermostatState) -> ThermostatState:
    return replace(f_hold_state, power=False, mode=ThermostatMode.SMART, setpoint=Decimal('5.0'))


def test_plan_change_is_empty_when_state_matches(f_hold_state: ThermostatState) -> None:
    assert plan_change(ThermostatTarget(power=True, setpoint=Decimal('22.0')), f_hold_state).is_empty


def test_plan_change_updates_only_setpoint(f_hold_state: ThermostatState) -> None:
    change = plan_change(ThermostatTarget(power=True, setpoint=Decimal('28.0')), f_hold_state)

    assert change == ThermostatChange(setpoint=Decimal('28.0'))


def test_plan_change_switches_smart_mode_to_hold(f_program_off_state: ThermostatState) -> None:
    change = plan_change(ThermostatTarget(power=True, setpoint=Decimal('18.0')), f_program_off_state)

    assert change == ThermostatChange(power=True, mode=ThermostatMode.HOLD, setpoint=Decimal('18.0'))


def test_plan_change_turns_off_only_powered_thermostat(
    f_hold_state: ThermostatState,
    f_program_off_state: ThermostatState,
) -> None:
    changes = (plan_change(OFF, f_hold_state), plan_change(OFF, f_program_off_state))

    assert changes == (ThermostatChange(power=False), ThermostatChange())
```

`app/apps/climate/domain/write_guard_tests.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from apps.climate.domain.write_guard import write_allowed

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ('last_command_at', 'expected'),
    [
        (None, True),
        (NOW - timedelta(minutes=29), False),
        (NOW - timedelta(minutes=30), True),
    ],
)
def test_write_allowed_respects_thirty_minutes(last_command_at: datetime | None, expected: bool) -> None:
    assert write_allowed(last_command_at, NOW) is expected
```

- [ ] **Step 3: Write the policies**

`app/apps/climate/domain/schedule.py`:

```python
from collections.abc import Sequence
from datetime import datetime, timedelta

from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.zone import ComfortWindow


def resolve_mode(windows: Sequence[ComfortWindow], now: datetime, preheat_lead: timedelta) -> ScheduleMode:
    occurrences = _occurrences(windows, now)
    if any(start <= now < end for start, end in occurrences):
        return ScheduleMode.COMFORT

    if any(start - preheat_lead <= now < start for start, _ in occurrences):
        return ScheduleMode.PREHEAT

    return ScheduleMode.NIGHT


def _occurrences(windows: Sequence[ComfortWindow], now: datetime) -> list[tuple[datetime, datetime]]:
    occurrences: list[tuple[datetime, datetime]] = []
    for offset in (-1, 0, 1):
        day = now.date() + timedelta(days=offset)
        occurrences.extend(
            (
                datetime.combine(day, window.start, tzinfo=now.tzinfo),
                datetime.combine(day, window.end, tzinfo=now.tzinfo),
            )
            for window in windows
            if window.weekday == day.weekday()
        )
    return occurrences
```

`app/apps/climate/domain/floor_policy.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.zone import ZoneSettings


@dataclass(frozen=True, kw_only=True)
class ThermostatTarget:
    power: bool
    setpoint: Decimal | None


def decide_floor_target(
    zone: ZoneSettings,
    mode: ScheduleMode,
    now: datetime,
    *,
    clock_trusted: bool,
) -> ThermostatTarget:
    if not zone.heating_enabled:
        return ThermostatTarget(power=False, setpoint=None)

    if zone.is_boosted(now):
        return ThermostatTarget(power=True, setpoint=zone.setpoints.boost)

    if not clock_trusted or mode is not ScheduleMode.NIGHT:
        return ThermostatTarget(power=True, setpoint=zone.setpoints.comfort)

    return ThermostatTarget(power=True, setpoint=zone.setpoints.night)
```

`app/apps/climate/domain/write_guard.py`:

```python
from datetime import datetime, timedelta

MIN_WRITE_INTERVAL = timedelta(minutes=30)


def write_allowed(
    last_command_at: datetime | None,
    now: datetime,
    min_interval: timedelta = MIN_WRITE_INTERVAL,
) -> bool:
    return last_command_at is None or now - last_command_at >= min_interval
```

- [ ] **Step 4: Lint, commit, push, watch CI**

```bash
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/apps/climate/domain
git commit -m 'Add climate domain policies for floor mode'
git push origin stage-1
sh scripts/ci-watch.sh
```

```json:metadata
{"files": ["app/apps/climate/domain/modes.py", "app/apps/climate/domain/zone.py", "app/apps/climate/domain/schedule.py", "app/apps/climate/domain/schedule_tests.py", "app/apps/climate/domain/floor_policy.py", "app/apps/climate/domain/floor_policy_tests.py", "app/apps/climate/domain/change.py", "app/apps/climate/domain/change_tests.py", "app/apps/climate/domain/write_guard.py", "app/apps/climate/domain/write_guard_tests.py", "app/apps/climate/domain/events.py", "app/apps/climate/domain/errors.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["daily 06:30-23:30 with 3h lead resolves comfort, pre-heat and night at the listed moments", "Friday 01:00 window: Thursday 22:00 pre-heat, Thursday 02:00 night", "floor target: night, comfort, untrusted clock, boost, expired boost, disabled zone", "plan_change: empty, setpoint only, smart to hold with power on, power off only when powered", "write_allowed false at 29 min, true at 30 min or None", "architecture tests pass; CI green"], "modelTier": "mechanical"}
```

---

### Task 10: Climate persistence — zones, comfort periods, repository

**Goal:** `Zone` and `ComfortPeriod` models with migration, admin and factories, and `ZoneRepository` that maps zones to `ZoneSettings`, records commands and sets boosts.

**Files:**
- Create: `app/apps/climate/models/__init__.py`, `app/apps/climate/models/zone.py`, `app/apps/climate/models/comfort_period.py`
- Create: `app/apps/climate/migrations/__init__.py`, `app/apps/climate/migrations/0001_initial.py` (generated)
- Create: `app/apps/climate/repositories/__init__.py`, `app/apps/climate/repositories/zone.py`, `app/apps/climate/repositories/zone_tests.py`
- Create: `app/apps/climate/admin.py`, `app/apps/climate/factories.py`

**Acceptance Criteria:**
- [ ] Zone defaults: comfort 28.0, night 22.0, boost 30.0, lead 180 minutes, heating enabled, live control off.
- [ ] A comfort period whose end is not after its start violates `comfort_period_end_after_start`.
- [ ] `all_settings()` maps every field, including thermostat slug, lead and windows; `boost()` sets `boost_until`, clears `last_command_at` and raises `ZoneNotFoundError` for unknown zones; `mark_commanded()` stores the time.
- [ ] `makemigrations --check` passes; CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Write the models**

`app/apps/climate/models/zone.py`:

```python
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.climate.domain.modes import ZoneKind

SETPOINT_VALIDATORS = [MinValueValidator(Decimal('5.0')), MaxValueValidator(Decimal('40.0'))]


class Zone(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=16, choices=[(kind.value, kind.value) for kind in ZoneKind], default=ZoneKind.AIR.value)
    thermostat = models.ForeignKey('devices.Device', on_delete=models.PROTECT, related_name='zones')
    heating_enabled = models.BooleanField(default=True)
    live_control = models.BooleanField(default=False)
    comfort_floor_setpoint = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('28.0'),
        validators=SETPOINT_VALIDATORS,
    )
    night_floor_setpoint = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('22.0'),
        validators=SETPOINT_VALIDATORS,
    )
    boost_floor_setpoint = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('30.0'),
        validators=SETPOINT_VALIDATORS,
    )
    preheat_lead_minutes = models.PositiveIntegerField(default=180, validators=[MaxValueValidator(240)])
    boost_until = models.DateTimeField(null=True, blank=True)
    last_command_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('slug',)

    def __str__(self) -> str:
        return self.name
```

`app/apps/climate/models/comfort_period.py`:

```python
from django.db import models

WEEKDAYS = [
    (0, 'Monday'),
    (1, 'Tuesday'),
    (2, 'Wednesday'),
    (3, 'Thursday'),
    (4, 'Friday'),
    (5, 'Saturday'),
    (6, 'Sunday'),
]


class ComfortPeriod(models.Model):
    zone = models.ForeignKey('climate.Zone', on_delete=models.CASCADE, related_name='comfort_periods')
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    start = models.TimeField()
    end = models.TimeField()

    class Meta:
        ordering = ('zone', 'weekday', 'start')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end__gt=models.F('start')),
                name='comfort_period_end_after_start',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.get_weekday_display()} {self.start:%H:%M}-{self.end:%H:%M}'
```

`app/apps/climate/models/__init__.py`:

```python
from apps.climate.models.comfort_period import ComfortPeriod
from apps.climate.models.zone import Zone

__all__ = ['ComfortPeriod', 'Zone']
```

- [ ] **Step 2: Write admin and factories**

`app/apps/climate/admin.py`:

```python
from django.contrib import admin

from apps.climate.models import ComfortPeriod, Zone


class ComfortPeriodInline(admin.TabularInline):
    model = ComfortPeriod
    extra = 0


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name', 'kind', 'thermostat', 'heating_enabled', 'live_control')
    list_editable = ('heating_enabled', 'live_control')
    inlines = (ComfortPeriodInline,)
```

`app/apps/climate/factories.py`:

```python
from datetime import time

import factory

from apps.climate.domain.modes import ZoneKind
from apps.climate.models import ComfortPeriod, Zone
from apps.devices.factories import DeviceFactory


class ZoneFactory(factory.django.DjangoModelFactory):
    slug = factory.Sequence(lambda n: f'zone-{n}')
    name = factory.Sequence(lambda n: f'Zone {n}')
    kind = ZoneKind.AIR
    thermostat = factory.SubFactory(DeviceFactory)

    class Meta:
        model = Zone


class ComfortPeriodFactory(factory.django.DjangoModelFactory):
    zone = factory.SubFactory(ZoneFactory)
    weekday = factory.Iterator(range(7))
    start = time(6, 30)
    end = time(23, 30)

    class Meta:
        model = ComfortPeriod
```

- [ ] **Step 3: Write the repository tests**

`app/apps/climate/repositories/__init__.py`: empty file.

`app/apps/climate/repositories/zone_tests.py`:

```python
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone import ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_zone() -> Zone:
    zone = ZoneFactory(slug='kitchen', name='Kitchen', thermostat__slug='thermostat-kitchen')
    ComfortPeriodFactory(zone=zone, weekday=0, start=time(6, 30), end=time(23, 30))
    return zone


@pytest.fixture
def f_commanded_zone(f_zone: Zone) -> Zone:
    Zone.objects.filter(pk=f_zone.pk).update(last_command_at=NOW - timedelta(minutes=5))
    return f_zone


def test_all_settings_maps_zone(f_zone: Zone) -> None:
    assert ZoneRepository.all_settings() == [
        ZoneSettings(
            slug=f_zone.slug,
            name='Kitchen',
            kind=ZoneKind.AIR,
            thermostat_slug='thermostat-kitchen',
            heating_enabled=True,
            live_control=False,
            setpoints=FloorSetpoints(comfort=Decimal('28.0'), night=Decimal('22.0'), boost=Decimal('30.0')),
            preheat_lead=timedelta(minutes=180),
            windows=(ComfortWindow(weekday=0, start=time(6, 30), end=time(23, 30)),),
            boost_until=None,
            last_command_at=None,
        ),
    ]


def test_mark_commanded_stores_time(f_zone: Zone) -> None:
    ZoneRepository.mark_commanded(f_zone.slug, NOW)

    assert Zone.objects.get(pk=f_zone.pk).last_command_at == NOW


def test_boost_sets_until_and_clears_last_command(f_commanded_zone: Zone) -> None:
    ZoneRepository.boost(f_commanded_zone.slug, NOW + timedelta(hours=2))

    zone = Zone.objects.get(pk=f_commanded_zone.pk)
    assert (zone.boost_until, zone.last_command_at) == (NOW + timedelta(hours=2), None)


def test_boost_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ZoneRepository.boost('attic', NOW)


def test_comfort_period_must_end_after_start(f_zone: Zone) -> None:
    with pytest.raises(IntegrityError):
        ComfortPeriodFactory(zone=f_zone, weekday=1, start=time(23, 0), end=time(6, 0))
```

- [ ] **Step 4: Write the repository**

`app/apps/climate/repositories/zone.py`:

```python
from datetime import datetime, timedelta

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone import ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.models import Zone


class ZoneRepository:
    @staticmethod
    def all_settings() -> list[ZoneSettings]:
        zones = Zone.objects.select_related('thermostat').prefetch_related('comfort_periods').order_by('slug')
        return [_to_settings(zone) for zone in zones]

    @staticmethod
    def mark_commanded(slug: str, at: datetime) -> None:
        Zone.objects.filter(slug=slug).update(last_command_at=at)

    @staticmethod
    def boost(slug: str, until: datetime) -> None:
        if Zone.objects.filter(slug=slug).update(boost_until=until, last_command_at=None) == 0:
            raise ZoneNotFoundError(slug)


def _to_settings(zone: Zone) -> ZoneSettings:
    return ZoneSettings(
        slug=zone.slug,
        name=zone.name,
        kind=ZoneKind(zone.kind),
        thermostat_slug=zone.thermostat.slug,
        heating_enabled=zone.heating_enabled,
        live_control=zone.live_control,
        setpoints=FloorSetpoints(
            comfort=zone.comfort_floor_setpoint,
            night=zone.night_floor_setpoint,
            boost=zone.boost_floor_setpoint,
        ),
        preheat_lead=timedelta(minutes=zone.preheat_lead_minutes),
        windows=tuple(
            ComfortWindow(weekday=period.weekday, start=period.start, end=period.end)
            for period in zone.comfort_periods.all()
        ),
        boost_until=zone.boost_until,
        last_command_at=zone.last_command_at,
    )
```

- [ ] **Step 5: Generate the migration, lint, commit, push, watch CI**

```bash
docker compose run --rm --no-deps app python manage.py makemigrations climate
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/apps/climate
git commit -m 'Add zones, comfort periods and zone repository'
git push origin stage-1
sh scripts/ci-watch.sh
```

Expected from makemigrations: `Migrations for 'climate': apps/climate/migrations/0001_initial.py + Create model Zone + Create model ComfortPeriod`.

```json:metadata
{"files": ["app/apps/climate/models/zone.py", "app/apps/climate/models/comfort_period.py", "app/apps/climate/migrations/0001_initial.py", "app/apps/climate/repositories/zone.py", "app/apps/climate/repositories/zone_tests.py", "app/apps/climate/admin.py", "app/apps/climate/factories.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["zone defaults comfort 28, night 22, boost 30, lead 180, heating on, live off", "comfort period end not after start violates the check constraint", "all_settings maps every field; boost sets until and clears last command or raises ZoneNotFoundError; mark_commanded stores time", "makemigrations --check passes; CI green"], "modelTier": "standard"}
```

---

### Task 11: Climate use cases — heating cycle and boost

**Goal:** `RunHeatingCycleUseCase` turning schedules into thermostat commands (or dry-run journal events) under the write budget, and `BoostZoneUseCase`.

**Files:**
- Create: `app/apps/climate/usecases/__init__.py`, `app/apps/climate/usecases/run_heating_cycle.py`, `app/apps/climate/usecases/run_heating_cycle_tests.py`
- Create: `app/apps/climate/usecases/boost_zone.py`, `app/apps/climate/usecases/boost_zone_tests.py`

**Acceptance Criteria:**
- [ ] At 07:00 a live zone reading 22.0 gets `setpoint 28.0`, outcome `applied`, a `ZoneCommandIssued(dry_run=False)` event and `last_command_at`.
- [ ] At 04:00 (pre-heat) the zone gets 28.0; at 01:00 with a 22.0 reading nothing is written (`unchanged`); with an untrusted clock at 01:00 it gets 28.0.
- [ ] Global dry-run or `live_control=False` yields `dry_run`, no writes and `ZoneCommandIssued(dry_run=True)`.
- [ ] A `smart` thermostat gets `mode hold` then `setpoint 22.0` at night.
- [ ] A command 10 minutes ago → `throttled`; a reading older than 10 minutes → `device_unavailable`; a failing write → `failed` plus `ZoneCommandFailed`.
- [ ] Boost stores `until = now + 2 h`, clears the write budget, emits `ZoneBoosted`, and raises `ZoneNotFoundError` for unknown zones.
- [ ] CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Write the heating cycle tests**

`app/apps/climate/usecases/__init__.py`: empty file.

`app/apps/climate/usecases/run_heating_cycle_tests.py`:

```python
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.events import ZoneCommandFailed, ZoneCommandIssued
from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.usecases.run_heating_cycle import (
    RunHeatingCycleInputDTO,
    RunHeatingCycleUseCase,
    ZoneCycleOutcome,
)
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.devices.domain.capabilities import FloorThermostat
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.services.state_store import ThermostatStateStore
from apps.devices.stubs import FloorThermostatStub, ThermostatProviderStub
from apps.system.stubs import ClockStub

pytestmark = pytest.mark.django_db

MOSCOW = ZoneInfo('Europe/Moscow')
MORNING = datetime(2026, 10, 29, 7, 0, tzinfo=MOSCOW)
PREHEAT = datetime(2026, 10, 29, 4, 0, tzinfo=MOSCOW)
NIGHT = datetime(2026, 10, 29, 1, 0, tzinfo=MOSCOW)


def _state(setpoint: str, mode: ThermostatMode = ThermostatMode.HOLD, read_at: datetime = MORNING) -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=mode,
        setpoint=Decimal(setpoint),
        floor_temperature=Decimal('21.0'),
        read_at=read_at,
    )


def _cycle(
    zone: Zone,
    state: ThermostatState,
    thermostat: FloorThermostat,
    dispatcher: EventDispatcher,
    *,
    dry_run: bool = False,
    clock_trusted: bool = True,
) -> RunHeatingCycleUseCase:
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_state(zone.thermostat.slug, state)
    return RunHeatingCycleUseCase(
        zones=ZoneRepository(),
        store=store,
        provider=ThermostatProviderStub(thermostats={zone.thermostat.slug: thermostat}),
        clock=ClockStub(trusted=clock_trusted),
        dry_run=dry_run,
        dispatcher=dispatcher,
    )


@pytest.fixture
def f_zone() -> Zone:
    zone = ZoneFactory(slug='kitchen', thermostat__slug='thermostat-kitchen', live_control=True)
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


@pytest.fixture
def f_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None)


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def dispatcher(f_events: list[DomainEvent]) -> EventDispatcher:
    event_dispatcher = EventDispatcher()
    event_dispatcher.register(ZoneCommandIssued, f_events.append)
    event_dispatcher.register(ZoneCommandFailed, f_events.append)
    return event_dispatcher


@pytest.fixture
def f_cycle(f_zone: Zone, f_thermostat: FloorThermostatStub, dispatcher: EventDispatcher) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher)


@pytest.fixture
def f_dry_run_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher, dry_run=True)


@pytest.fixture
def f_observed_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(live_control=False)
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher)


@pytest.fixture
def f_untrusted_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher, clock_trusted=False)


@pytest.fixture
def f_program_mode_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('5.0', mode=ThermostatMode.SMART), f_thermostat, dispatcher)


@pytest.fixture
def f_recent_command_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(last_command_at=MORNING - timedelta(minutes=10))
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher)


@pytest.fixture
def f_stale_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0', read_at=MORNING - timedelta(minutes=11)), f_thermostat, dispatcher)


@pytest.fixture
def f_failing_cycle(f_zone: Zone, dispatcher: EventDispatcher) -> RunHeatingCycleUseCase:
    thermostat = FloorThermostatStub(state=None, error=DeviceUnavailableError('timeout'))
    return _cycle(f_zone, _state('22.0'), thermostat, dispatcher)


def test_cycle_applies_comfort_setpoint(
    f_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_zone: Zone,
) -> None:
    output = f_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

    assert (output.outcomes, f_thermostat.commands, f_events, Zone.objects.get(pk=f_zone.pk).last_command_at) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                dry_run=False,
                occurred_at=MORNING,
            ),
        ],
        MORNING,
    )


def test_cycle_preheats_before_comfort(f_cycle: RunHeatingCycleUseCase, f_thermostat: FloorThermostatStub) -> None:
    f_cycle.execute(RunHeatingCycleInputDTO(now=PREHEAT))

    assert f_thermostat.commands == [('setpoint', Decimal('28.0'))]


def test_cycle_leaves_matching_thermostat_alone(
    f_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.UNCHANGED}, [])


def test_cycle_holds_comfort_when_clock_untrusted(
    f_untrusted_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_untrusted_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT))

    assert f_thermostat.commands == [('setpoint', Decimal('28.0'))]


def test_dry_run_journals_without_writing(
    f_dry_run_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_dry_run_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

    assert (output.outcomes, f_thermostat.commands, f_events) == (
        {'kitchen': ZoneCycleOutcome.DRY_RUN},
        [],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                dry_run=True,
                occurred_at=MORNING,
            ),
        ],
    )


def test_zone_without_live_control_stays_in_dry_run(
    f_observed_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_observed_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.DRY_RUN}, [])


def test_cycle_switches_program_mode_to_hold(
    f_program_mode_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_program_mode_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT))

    assert f_thermostat.commands == [('mode', ThermostatMode.HOLD), ('setpoint', Decimal('22.0'))]


def test_cycle_throttles_zone_commanded_recently(
    f_recent_command_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_recent_command_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.THROTTLED}, [])


def test_cycle_skips_unavailable_thermostat(
    f_stale_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_stale_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.DEVICE_UNAVAILABLE}, [])


def test_cycle_reports_failed_write(f_failing_cycle: RunHeatingCycleUseCase, f_events: list[DomainEvent]) -> None:
    output = f_failing_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

    assert (output.outcomes, f_events) == (
        {'kitchen': ZoneCycleOutcome.FAILED},
        [
            ZoneCommandFailed(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                error='timeout',
                occurred_at=MORNING,
            ),
        ],
    )
```

- [ ] **Step 2: Write the heating cycle**

`app/apps/climate/usecases/run_heating_cycle.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from django.conf import settings
from django.utils import timezone

from apps.climate.domain.change import ThermostatChange, plan_change
from apps.climate.domain.events import ZoneCommandFailed, ZoneCommandIssued
from apps.climate.domain.floor_policy import decide_floor_target
from apps.climate.domain.schedule import resolve_mode
from apps.climate.domain.write_guard import write_allowed
from apps.climate.domain.zone import ZoneSettings
from apps.climate.repositories.zone import ZoneRepository
from apps.core.domain.errors import DomainError
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.capabilities import ThermostatProvider
from apps.devices.services.driver_registry import driver_registry
from apps.devices.services.state_store import ThermostatStateStore, thermostat_state_store
from apps.system.domain.clock import ClockTrustSource
from apps.system.services.clock_trust import clock_trust_source


class ZoneCycleOutcome(StrEnum):
    UNCHANGED = 'unchanged'
    APPLIED = 'applied'
    DRY_RUN = 'dry_run'
    THROTTLED = 'throttled'
    DEVICE_UNAVAILABLE = 'device_unavailable'
    FAILED = 'failed'


@dataclass(frozen=True, kw_only=True)
class RunHeatingCycleInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class RunHeatingCycleOutputDTO:
    outcomes: dict[str, ZoneCycleOutcome]


class RunHeatingCycleUseCase(BaseUseCase[RunHeatingCycleInputDTO, RunHeatingCycleOutputDTO]):
    def __init__(
        self,
        zones: ZoneRepository,
        store: ThermostatStateStore,
        provider: ThermostatProvider,
        clock: ClockTrustSource,
        dry_run: bool,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._zones = zones
        self._store = store
        self._provider = provider
        self._clock = clock
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'RunHeatingCycleUseCase':
        return cls(
            zones=ZoneRepository(),
            store=thermostat_state_store,
            provider=driver_registry,
            clock=clock_trust_source(),
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def _execute(self, input_dto: RunHeatingCycleInputDTO) -> RunHeatingCycleOutputDTO:
        now = timezone.localtime(input_dto.now)
        clock_trusted = self._clock.status().trusted
        return RunHeatingCycleOutputDTO(
            outcomes={zone.slug: self._run_zone(zone, now, clock_trusted) for zone in self._zones.all_settings()},
        )

    def _run_zone(self, zone: ZoneSettings, now: datetime, clock_trusted: bool) -> ZoneCycleOutcome:
        snapshot = self._store.snapshot(zone.thermostat_slug, now)
        if not snapshot.available or snapshot.state is None:
            return ZoneCycleOutcome.DEVICE_UNAVAILABLE

        mode = resolve_mode(zone.windows, now, zone.preheat_lead)
        target = decide_floor_target(zone, mode, now, clock_trusted=clock_trusted)
        change = plan_change(target, snapshot.state)
        if change.is_empty:
            return ZoneCycleOutcome.UNCHANGED

        if not write_allowed(zone.last_command_at, now):
            return ZoneCycleOutcome.THROTTLED

        return self._issue(zone, change, now)

    def _issue(self, zone: ZoneSettings, change: ThermostatChange, now: datetime) -> ZoneCycleOutcome:
        self._zones.mark_commanded(zone.slug, now)
        dry_run = self._dry_run or not zone.live_control
        if dry_run:
            self._event_store.add_event(ZoneCommandIssued(zone=zone.slug, change=change, dry_run=True, occurred_at=now))
            return ZoneCycleOutcome.DRY_RUN

        try:
            self._apply(zone.thermostat_slug, change)
        except DomainError as error:
            self._event_store.add_event(
                ZoneCommandFailed(zone=zone.slug, change=change, error=error.detail, occurred_at=now),
            )
            return ZoneCycleOutcome.FAILED

        self._event_store.add_event(ZoneCommandIssued(zone=zone.slug, change=change, dry_run=False, occurred_at=now))
        return ZoneCycleOutcome.APPLIED

    def _apply(self, slug: str, change: ThermostatChange) -> None:
        thermostat = self._provider.thermostat(slug)
        if change.power is not None:
            thermostat.set_power(change.power)
        if change.mode is not None:
            thermostat.set_mode(change.mode)
        if change.setpoint is not None:
            thermostat.set_setpoint(change.setpoint)
```

- [ ] **Step 3: Write the boost tests**

`app/apps/climate/usecases/boost_zone_tests.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.events import ZoneBoosted
from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.usecases.boost_zone import BoostZoneInputDTO, BoostZoneOutputDTO, BoostZoneUseCase
from apps.core.domain.events import DomainEvent, EventDispatcher

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_zone() -> Zone:
    return ZoneFactory(slug='bathroom-1f', last_command_at=NOW - timedelta(minutes=5))


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_use_case(f_events: list[DomainEvent]) -> BoostZoneUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(ZoneBoosted, f_events.append)
    return BoostZoneUseCase(zones=ZoneRepository(), dispatcher=dispatcher)


def test_boost_sets_two_hours_and_clears_write_budget(
    f_use_case: BoostZoneUseCase,
    f_zone: Zone,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(BoostZoneInputDTO(zone=f_zone.slug, now=NOW))

    zone = Zone.objects.get(pk=f_zone.pk)
    assert (output, zone.boost_until, zone.last_command_at, f_events) == (
        BoostZoneOutputDTO(zone='bathroom-1f', until=NOW + timedelta(hours=2)),
        NOW + timedelta(hours=2),
        None,
        [ZoneBoosted(zone='bathroom-1f', until=NOW + timedelta(hours=2), occurred_at=NOW)],
    )


def test_boost_unknown_zone_raises(f_use_case: BoostZoneUseCase, f_events: list[DomainEvent]) -> None:
    with pytest.raises(ZoneNotFoundError):
        f_use_case.execute(BoostZoneInputDTO(zone='attic', now=NOW))

    assert f_events == []
```

- [ ] **Step 4: Write the boost use case**

`app/apps/climate/usecases/boost_zone.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.climate.domain.events import ZoneBoosted
from apps.climate.repositories.zone import ZoneRepository
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase

BOOST_DURATION = timedelta(hours=2)


@dataclass(frozen=True, kw_only=True)
class BoostZoneInputDTO:
    zone: str
    now: datetime


@dataclass(frozen=True, kw_only=True)
class BoostZoneOutputDTO:
    zone: str
    until: datetime


class BoostZoneUseCase(BaseUseCase[BoostZoneInputDTO, BoostZoneOutputDTO]):
    def __init__(self, zones: ZoneRepository, dispatcher: EventDispatcher = event_dispatcher) -> None:
        super().__init__(dispatcher=dispatcher)
        self._zones = zones

    @classmethod
    def init(cls) -> 'BoostZoneUseCase':
        return cls(zones=ZoneRepository())

    def _execute(self, input_dto: BoostZoneInputDTO) -> BoostZoneOutputDTO:
        until = input_dto.now + BOOST_DURATION
        self._zones.boost(input_dto.zone, until)
        self._event_store.add_event(ZoneBoosted(zone=input_dto.zone, until=until, occurred_at=input_dto.now))
        return BoostZoneOutputDTO(zone=input_dto.zone, until=until)
```

- [ ] **Step 5: Lint, commit, push, watch CI**

```bash
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/apps/climate/usecases
git commit -m 'Add heating cycle and boost use cases'
git push origin stage-1
sh scripts/ci-watch.sh
```

```json:metadata
{"files": ["app/apps/climate/usecases/run_heating_cycle.py", "app/apps/climate/usecases/run_heating_cycle_tests.py", "app/apps/climate/usecases/boost_zone.py", "app/apps/climate/usecases/boost_zone_tests.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["07:00 live zone at 22.0 gets setpoint 28.0, applied, ZoneCommandIssued(dry_run=False), last_command_at set", "04:00 pre-heat 28.0; 01:00 unchanged; untrusted clock at 01:00 gets 28.0", "global dry-run or live_control False yields dry_run, no writes, ZoneCommandIssued(dry_run=True)", "smart thermostat gets mode hold then setpoint 22.0 at night", "command 10 min ago throttled; reading older than 10 min device_unavailable; failing write failed with ZoneCommandFailed", "boost stores now+2h, clears write budget, emits ZoneBoosted, raises ZoneNotFoundError for unknown zones", "CI green"], "modelTier": "standard"}
```

---

### Task 12: Telemetry — journal, samples, recorder, event handlers

**Goal:** Journal entries and samples with repositories, an in-memory sample buffer, journal handlers for device and zone events, and use cases to capture samples, flush them in batches and journal startups.

**Files:**
- Create: `app/apps/telemetry/domain/__init__.py`, `app/apps/telemetry/domain/samples.py`
- Create: `app/apps/telemetry/models/__init__.py`, `app/apps/telemetry/models/journal_entry.py`, `app/apps/telemetry/models/sample.py`
- Create: `app/apps/telemetry/migrations/__init__.py`, `app/apps/telemetry/migrations/0001_initial.py` (generated)
- Create: `app/apps/telemetry/repositories/__init__.py`, `journal.py`, `journal_tests.py`, `sample.py`, `sample_tests.py` (under `app/apps/telemetry/repositories/`)
- Create: `app/apps/telemetry/services/__init__.py`, `app/apps/telemetry/services/sample_buffer.py`, `app/apps/telemetry/services/sample_buffer_tests.py`
- Create: `app/apps/telemetry/event_handlers.py`, `app/apps/telemetry/event_handlers_tests.py`
- Create: `app/apps/telemetry/usecases/__init__.py`, `capture_samples.py`, `capture_samples_tests.py`, `flush_samples.py`, `flush_samples_tests.py`, `journal_startup.py`, `journal_startup_tests.py` (under `app/apps/telemetry/usecases/`)
- Create: `app/apps/telemetry/admin.py`, `app/apps/telemetry/factories.py`
- Modify: `app/apps/telemetry/apps.py`

**Acceptance Criteria:**
- [ ] `ZoneCommandIssued` is journaled as `zone_command_issued` with payload `{'zone', 'change': {'power', 'mode', 'setpoint'}, 'dry_run'}`; `DeviceAvailabilityChanged`, `ZoneCommandFailed` and `ZoneBoosted` are journaled under their snake_case kinds.
- [ ] Capture buffers floor temperature, setpoint and power for available thermostats only; flush writes buffered samples in one `bulk_create`; the buffer drains once.
- [ ] Startup journals `teplo_started` with the latest of the last journal entry and the last sample.
- [ ] `makemigrations --check` passes; CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Write the domain value, models, admin and factory**

`app/apps/telemetry/domain/__init__.py`: empty file.

`app/apps/telemetry/domain/samples.py`:

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, kw_only=True)
class SampleValue:
    metric: str
    subject: str
    value: float
    recorded_at: datetime
```

`app/apps/telemetry/models/journal_entry.py`:

```python
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class JournalEntry(models.Model):
    kind = models.CharField(max_length=64)
    subject = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ('-created_at', '-id')
        indexes = [models.Index(fields=('-created_at',), name='journal_created_idx')]

    def __str__(self) -> str:
        return f'{self.created_at:%Y-%m-%d %H:%M:%S} {self.kind} {self.subject}'
```

`app/apps/telemetry/models/sample.py`:

```python
from django.db import models


class Sample(models.Model):
    metric = models.CharField(max_length=64)
    subject = models.CharField(max_length=64)
    value = models.FloatField()
    recorded_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=('metric', 'subject', 'recorded_at'), name='sample_series_idx')]

    def __str__(self) -> str:
        return f'{self.metric} {self.subject} {self.value}'
```

`app/apps/telemetry/models/__init__.py`:

```python
from apps.telemetry.models.journal_entry import JournalEntry
from apps.telemetry.models.sample import Sample

__all__ = ['JournalEntry', 'Sample']
```

`app/apps/telemetry/admin.py`:

```python
from django.contrib import admin

from apps.telemetry.models import JournalEntry


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'kind', 'subject')
    list_filter = ('kind',)
    readonly_fields = ('created_at', 'kind', 'subject', 'payload')
```

`app/apps/telemetry/factories.py`:

```python
import factory
from django.utils import timezone

from apps.telemetry.models import JournalEntry


class JournalEntryFactory(factory.django.DjangoModelFactory):
    kind = 'zone_boosted'
    subject = 'kitchen'
    payload = factory.LazyFunction(dict)
    created_at = factory.LazyFunction(timezone.now)

    class Meta:
        model = JournalEntry
```

- [ ] **Step 2: Write repository, buffer and handler tests**

`app/apps/telemetry/repositories/__init__.py`, `app/apps/telemetry/services/__init__.py`, `app/apps/telemetry/usecases/__init__.py`: empty files.

`app/apps/telemetry/repositories/journal_tests.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from apps.telemetry.factories import JournalEntryFactory
from apps.telemetry.models import JournalEntry
from apps.telemetry.repositories.journal import JournalRepository

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_entries() -> list[JournalEntry]:
    return [
        JournalEntryFactory(kind='teplo_started', created_at=NOW - timedelta(hours=1)),
        JournalEntryFactory(kind='zone_boosted', created_at=NOW),
    ]


def test_latest_returns_newest_first(f_entries: list[JournalEntry]) -> None:
    assert JournalRepository.latest(limit=1) == [f_entries[1]]


def test_last_activity_is_newest_entry_time(f_entries: list[JournalEntry]) -> None:
    assert JournalRepository.last_activity() == f_entries[1].created_at


def test_add_creates_entry() -> None:
    JournalRepository.add(kind='teplo_started', subject='', payload={'last_activity': None}, created_at=NOW)

    assert list(JournalEntry.objects.values_list('kind', 'payload', 'created_at')) == [
        ('teplo_started', {'last_activity': None}, NOW),
    ]
```

`app/apps/telemetry/repositories/sample_tests.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.models import Sample
from apps.telemetry.repositories.sample import SampleRepository

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_samples() -> list[SampleValue]:
    return [
        SampleValue(metric='floor_temperature', subject='thermostat-kitchen', value=24.5, recorded_at=NOW),
        SampleValue(
            metric='floor_temperature',
            subject='thermostat-kitchen',
            value=24.0,
            recorded_at=NOW - timedelta(minutes=1),
        ),
    ]


def test_add_many_persists_samples(f_samples: list[SampleValue]) -> None:
    created = SampleRepository.add_many(f_samples)

    assert (created, Sample.objects.count(), SampleRepository.last_recorded_at()) == (2, 2, NOW)


def test_last_recorded_at_is_none_without_samples() -> None:
    assert SampleRepository.last_recorded_at() is None
```

`app/apps/telemetry/services/sample_buffer_tests.py`:

```python
from datetime import UTC, datetime

import pytest

from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.services.sample_buffer import SampleBuffer


@pytest.fixture
def f_sample() -> SampleValue:
    return SampleValue(
        metric='floor_temperature',
        subject='thermostat-kitchen',
        value=24.5,
        recorded_at=datetime(2026, 10, 29, 6, 0, tzinfo=UTC),
    )


@pytest.fixture
def f_buffer(f_sample: SampleValue) -> SampleBuffer:
    buffer = SampleBuffer()
    buffer.add(f_sample)
    return buffer


def test_drain_returns_samples_once(f_buffer: SampleBuffer, f_sample: SampleValue) -> None:
    assert (f_buffer.drain(), f_buffer.drain()) == ([f_sample], [])
```

`app/apps/telemetry/event_handlers_tests.py`:

```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.events import ZoneBoosted, ZoneCommandFailed, ZoneCommandIssued
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.readings import ThermostatMode
from apps.telemetry.event_handlers import (
    journal_device_availability,
    journal_zone_boost,
    journal_zone_command,
    journal_zone_command_failure,
)
from apps.telemetry.models import JournalEntry

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


def _entry() -> tuple[str, str, object, datetime]:
    entry = JournalEntry.objects.get()
    return entry.kind, entry.subject, entry.payload, entry.created_at


def test_zone_command_is_journaled_with_change() -> None:
    journal_zone_command(
        ZoneCommandIssued(
            zone='kitchen',
            change=ThermostatChange(mode=ThermostatMode.HOLD, setpoint=Decimal('28.0')),
            dry_run=True,
            occurred_at=NOW,
        ),
    )

    assert _entry() == (
        'zone_command_issued',
        'kitchen',
        {'zone': 'kitchen', 'change': {'power': None, 'mode': 'hold', 'setpoint': '28.0'}, 'dry_run': True},
        NOW,
    )


def test_zone_command_failure_is_journaled() -> None:
    journal_zone_command_failure(
        ZoneCommandFailed(zone='kitchen', change=ThermostatChange(power=False), error='timeout', occurred_at=NOW),
    )

    assert _entry() == (
        'zone_command_failed',
        'kitchen',
        {'zone': 'kitchen', 'change': {'power': False, 'mode': None, 'setpoint': None}, 'error': 'timeout'},
        NOW,
    )


def test_device_availability_is_journaled() -> None:
    journal_device_availability(
        DeviceAvailabilityChanged(slug='thermostat-kitchen', available=False, reason='timeout', occurred_at=NOW),
    )

    assert _entry() == (
        'device_availability_changed',
        'thermostat-kitchen',
        {'slug': 'thermostat-kitchen', 'available': False, 'reason': 'timeout'},
        NOW,
    )


def test_zone_boost_is_journaled() -> None:
    journal_zone_boost(ZoneBoosted(zone='bathroom-1f', until=NOW + timedelta(hours=2), occurred_at=NOW))

    assert _entry() == ('zone_boosted', 'bathroom-1f', {'zone': 'bathroom-1f', 'until': '2026-10-29T08:00:00Z'}, NOW)
```

- [ ] **Step 3: Write repositories, buffer and handlers**

`app/apps/telemetry/repositories/journal.py`:

```python
from datetime import datetime

from django.db.models import Max

from apps.telemetry.models import JournalEntry


class JournalRepository:
    @staticmethod
    def add(kind: str, subject: str, payload: dict[str, object], created_at: datetime) -> None:
        JournalEntry.objects.create(kind=kind, subject=subject, payload=payload, created_at=created_at)

    @staticmethod
    def latest(limit: int) -> list[JournalEntry]:
        return list(JournalEntry.objects.all()[:limit])

    @staticmethod
    def last_activity() -> datetime | None:
        return JournalEntry.objects.aggregate(last=Max('created_at'))['last']
```

`app/apps/telemetry/repositories/sample.py`:

```python
from collections.abc import Sequence
from datetime import datetime

from django.db.models import Max

from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.models import Sample


class SampleRepository:
    @staticmethod
    def add_many(samples: Sequence[SampleValue]) -> int:
        created = Sample.objects.bulk_create(
            [
                Sample(metric=sample.metric, subject=sample.subject, value=sample.value, recorded_at=sample.recorded_at)
                for sample in samples
            ],
        )
        return len(created)

    @staticmethod
    def last_recorded_at() -> datetime | None:
        return Sample.objects.aggregate(last=Max('recorded_at'))['last']
```

`app/apps/telemetry/services/sample_buffer.py`:

```python
import threading

from apps.telemetry.domain.samples import SampleValue


class SampleBuffer:
    def __init__(self) -> None:
        self._samples: list[SampleValue] = []
        self._lock = threading.Lock()

    def add(self, sample: SampleValue) -> None:
        with self._lock:
            self._samples.append(sample)

    def drain(self) -> list[SampleValue]:
        with self._lock:
            samples = self._samples
            self._samples = []
        return samples


sample_buffer = SampleBuffer()
```

`app/apps/telemetry/event_handlers.py`:

```python
from dataclasses import asdict

from apps.climate.domain.events import ZoneBoosted, ZoneCommandFailed, ZoneCommandIssued
from apps.core.domain.events import DomainEvent, domain_event_handler
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.telemetry.repositories.journal import JournalRepository


def _payload(event: DomainEvent) -> dict[str, object]:
    payload = asdict(event)
    payload.pop('occurred_at')
    return payload


@domain_event_handler(DeviceAvailabilityChanged)
def journal_device_availability(event: DeviceAvailabilityChanged) -> None:
    JournalRepository.add(
        kind='device_availability_changed',
        subject=event.slug,
        payload=_payload(event),
        created_at=event.occurred_at,
    )


@domain_event_handler(ZoneCommandIssued)
def journal_zone_command(event: ZoneCommandIssued) -> None:
    JournalRepository.add(
        kind='zone_command_issued',
        subject=event.zone,
        payload=_payload(event),
        created_at=event.occurred_at,
    )


@domain_event_handler(ZoneCommandFailed)
def journal_zone_command_failure(event: ZoneCommandFailed) -> None:
    JournalRepository.add(
        kind='zone_command_failed',
        subject=event.zone,
        payload=_payload(event),
        created_at=event.occurred_at,
    )


@domain_event_handler(ZoneBoosted)
def journal_zone_boost(event: ZoneBoosted) -> None:
    JournalRepository.add(
        kind='zone_boosted',
        subject=event.zone,
        payload=_payload(event),
        created_at=event.occurred_at,
    )
```

`app/apps/telemetry/apps.py`:

```python
from django.apps import AppConfig


class TelemetryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.telemetry'

    def ready(self) -> None:
        from apps.telemetry import event_handlers  # noqa: F401
```

- [ ] **Step 4: Write the use case tests**

`app/apps/telemetry/usecases/capture_samples_tests.py`:

```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import ThermostatStateStore
from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.services.sample_buffer import SampleBuffer
from apps.telemetry.usecases.capture_samples import (
    CaptureSamplesInputDTO,
    CaptureSamplesOutputDTO,
    CaptureSamplesUseCase,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_buffer() -> SampleBuffer:
    return SampleBuffer()


@pytest.fixture
def f_use_case(f_buffer: SampleBuffer) -> CaptureSamplesUseCase:
    DeviceFactory(slug='thermostat-kitchen')
    DeviceFactory(slug='thermostat-hallway')
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_state(
        'thermostat-kitchen',
        ThermostatState(
            power=True,
            mode=ThermostatMode.HOLD,
            setpoint=Decimal('28.0'),
            floor_temperature=Decimal('24.5'),
            read_at=NOW,
        ),
    )
    return CaptureSamplesUseCase(devices=DeviceRepository(), store=store, buffer=f_buffer)


def test_capture_buffers_available_thermostats_only(f_use_case: CaptureSamplesUseCase, f_buffer: SampleBuffer) -> None:
    output = f_use_case.execute(CaptureSamplesInputDTO(now=NOW))

    assert (output, f_buffer.drain()) == (
        CaptureSamplesOutputDTO(captured=3),
        [
            SampleValue(metric='floor_temperature', subject='thermostat-kitchen', value=24.5, recorded_at=NOW),
            SampleValue(metric='floor_setpoint', subject='thermostat-kitchen', value=28.0, recorded_at=NOW),
            SampleValue(metric='thermostat_power', subject='thermostat-kitchen', value=1.0, recorded_at=NOW),
        ],
    )
```

`app/apps/telemetry/usecases/flush_samples_tests.py`:

```python
from datetime import UTC, datetime

import pytest

from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.models import Sample
from apps.telemetry.repositories.sample import SampleRepository
from apps.telemetry.services.sample_buffer import SampleBuffer
from apps.telemetry.usecases.flush_samples import FlushSamplesInputDTO, FlushSamplesOutputDTO, FlushSamplesUseCase

pytestmark = pytest.mark.django_db


@pytest.fixture
def f_use_case() -> FlushSamplesUseCase:
    buffer = SampleBuffer()
    buffer.add(
        SampleValue(
            metric='floor_temperature',
            subject='thermostat-kitchen',
            value=24.5,
            recorded_at=datetime(2026, 10, 29, 6, 0, tzinfo=UTC),
        ),
    )
    return FlushSamplesUseCase(buffer=buffer, samples=SampleRepository())


def test_flush_persists_buffered_samples(f_use_case: FlushSamplesUseCase) -> None:
    output = f_use_case.execute(FlushSamplesInputDTO())

    assert (output, list(Sample.objects.values_list('metric', 'subject', 'value'))) == (
        FlushSamplesOutputDTO(flushed=1),
        [('floor_temperature', 'thermostat-kitchen', 24.5)],
    )
```

`app/apps/telemetry/usecases/journal_startup_tests.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from django.core.serializers.json import DjangoJSONEncoder

from apps.telemetry.factories import JournalEntryFactory
from apps.telemetry.models import JournalEntry
from apps.telemetry.repositories.journal import JournalRepository
from apps.telemetry.repositories.sample import SampleRepository
from apps.telemetry.usecases.journal_startup import (
    JournalStartupInputDTO,
    JournalStartupOutputDTO,
    JournalStartupUseCase,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_previous_entry() -> JournalEntry:
    return JournalEntryFactory(kind='zone_boosted', created_at=NOW - timedelta(hours=2))


@pytest.fixture
def f_use_case() -> JournalStartupUseCase:
    return JournalStartupUseCase(journal=JournalRepository(), samples=SampleRepository())


def test_startup_journals_last_activity(f_use_case: JournalStartupUseCase, f_previous_entry: JournalEntry) -> None:
    output = f_use_case.execute(JournalStartupInputDTO(now=NOW))

    started = JournalEntry.objects.get(kind='teplo_started')
    assert (output, started.payload, started.created_at) == (
        JournalStartupOutputDTO(last_activity=f_previous_entry.created_at),
        {'last_activity': DjangoJSONEncoder().default(f_previous_entry.created_at)},
        NOW,
    )
```

- [ ] **Step 5: Write the use cases**

`app/apps/telemetry/usecases/capture_samples.py`:

```python
from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.readings import ThermostatState
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import ThermostatStateStore, thermostat_state_store
from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.services.sample_buffer import SampleBuffer, sample_buffer


@dataclass(frozen=True, kw_only=True)
class CaptureSamplesInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class CaptureSamplesOutputDTO:
    captured: int


class CaptureSamplesUseCase(BaseUseCase[CaptureSamplesInputDTO, CaptureSamplesOutputDTO]):
    def __init__(self, devices: DeviceRepository, store: ThermostatStateStore, buffer: SampleBuffer) -> None:
        super().__init__()
        self._devices = devices
        self._store = store
        self._buffer = buffer

    @classmethod
    def init(cls) -> 'CaptureSamplesUseCase':
        return cls(devices=DeviceRepository(), store=thermostat_state_store, buffer=sample_buffer)

    def _execute(self, input_dto: CaptureSamplesInputDTO) -> CaptureSamplesOutputDTO:
        captured = 0
        for slug in self._devices.thermostat_slugs():
            snapshot = self._store.snapshot(slug, input_dto.now)
            if not snapshot.available or snapshot.state is None:
                continue

            for sample in _thermostat_samples(slug, snapshot.state, input_dto.now):
                self._buffer.add(sample)
                captured += 1
        return CaptureSamplesOutputDTO(captured=captured)


def _thermostat_samples(slug: str, state: ThermostatState, now: datetime) -> tuple[SampleValue, ...]:
    return (
        SampleValue(metric='floor_temperature', subject=slug, value=float(state.floor_temperature), recorded_at=now),
        SampleValue(metric='floor_setpoint', subject=slug, value=float(state.setpoint), recorded_at=now),
        SampleValue(metric='thermostat_power', subject=slug, value=1.0 if state.power else 0.0, recorded_at=now),
    )
```

`app/apps/telemetry/usecases/flush_samples.py`:

```python
from dataclasses import dataclass

from apps.core.domain.usecases.base import BaseUseCase
from apps.telemetry.repositories.sample import SampleRepository
from apps.telemetry.services.sample_buffer import SampleBuffer, sample_buffer


@dataclass(frozen=True, kw_only=True)
class FlushSamplesInputDTO:
    pass


@dataclass(frozen=True, kw_only=True)
class FlushSamplesOutputDTO:
    flushed: int


class FlushSamplesUseCase(BaseUseCase[FlushSamplesInputDTO, FlushSamplesOutputDTO]):
    def __init__(self, buffer: SampleBuffer, samples: SampleRepository) -> None:
        super().__init__()
        self._buffer = buffer
        self._samples = samples

    @classmethod
    def init(cls) -> 'FlushSamplesUseCase':
        return cls(buffer=sample_buffer, samples=SampleRepository())

    def _execute(self, input_dto: FlushSamplesInputDTO) -> FlushSamplesOutputDTO:
        samples = self._buffer.drain()
        if not samples:
            return FlushSamplesOutputDTO(flushed=0)

        return FlushSamplesOutputDTO(flushed=self._samples.add_many(samples))
```

`app/apps/telemetry/usecases/journal_startup.py`:

```python
from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.usecases.base import BaseUseCase
from apps.telemetry.repositories.journal import JournalRepository
from apps.telemetry.repositories.sample import SampleRepository


@dataclass(frozen=True, kw_only=True)
class JournalStartupInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class JournalStartupOutputDTO:
    last_activity: datetime | None


class JournalStartupUseCase(BaseUseCase[JournalStartupInputDTO, JournalStartupOutputDTO]):
    def __init__(self, journal: JournalRepository, samples: SampleRepository) -> None:
        super().__init__()
        self._journal = journal
        self._samples = samples

    @classmethod
    def init(cls) -> 'JournalStartupUseCase':
        return cls(journal=JournalRepository(), samples=SampleRepository())

    def _execute(self, input_dto: JournalStartupInputDTO) -> JournalStartupOutputDTO:
        moments = [moment for moment in (self._journal.last_activity(), self._samples.last_recorded_at()) if moment]
        last_activity = max(moments, default=None)
        self._journal.add(
            kind='teplo_started',
            subject='',
            payload={'last_activity': last_activity},
            created_at=input_dto.now,
        )
        return JournalStartupOutputDTO(last_activity=last_activity)
```

- [ ] **Step 6: Generate the migration, lint, commit, push, watch CI**

```bash
docker compose run --rm --no-deps app python manage.py makemigrations telemetry
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/apps/telemetry
git commit -m 'Add journal, samples and telemetry use cases'
git push origin stage-1
sh scripts/ci-watch.sh
```

```json:metadata
{"files": ["app/apps/telemetry/domain/samples.py", "app/apps/telemetry/models/journal_entry.py", "app/apps/telemetry/models/sample.py", "app/apps/telemetry/migrations/0001_initial.py", "app/apps/telemetry/repositories/journal.py", "app/apps/telemetry/repositories/sample.py", "app/apps/telemetry/services/sample_buffer.py", "app/apps/telemetry/event_handlers.py", "app/apps/telemetry/usecases/capture_samples.py", "app/apps/telemetry/usecases/flush_samples.py", "app/apps/telemetry/usecases/journal_startup.py", "app/apps/telemetry/apps.py", "app/apps/telemetry/admin.py", "app/apps/telemetry/factories.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["zone_command_issued payload has zone, change {power, mode, setpoint} and dry_run; other events journaled under snake_case kinds", "capture buffers three samples per available thermostat only; flush bulk-creates; buffer drains once", "startup journals teplo_started with the latest activity", "makemigrations --check passes; CI green"], "modelTier": "standard"}
```

---

### Task 13: Runtime — periodic jobs, SQLite tuning, `run_teplo`

**Goal:** One process that journals startup, runs polling (30 s), sample capture (1 min), the heating cycle (1 min) and sample flush (5 min) in worker threads, serves the Django ASGI app through uvicorn, flushes samples on shutdown, and opens SQLite in WAL mode.

**Files:**
- Create: `app/apps/core/signals.py`, `app/apps/core/signals_tests.py`
- Modify: `app/apps/core/apps.py`
- Create: `app/apps/core/runtime/__init__.py`, `app/apps/core/runtime/periodic.py`, `app/apps/core/runtime/periodic_tests.py`, `app/apps/core/runtime/server.py`
- Create: `app/apps/system/services/jobs.py`, `app/apps/system/services/jobs_tests.py`, `app/apps/system/services/runtime.py`
- Create: `app/apps/system/management/__init__.py`, `app/apps/system/management/commands/__init__.py`, `app/apps/system/management/commands/run_teplo.py`

**Acceptance Criteria:**
- [ ] A new SQLite connection executes `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL`; other vendors execute nothing.
- [ ] `JobRunner.run_once` runs the action and survives an action that raises.
- [ ] `teplo_jobs()` returns poll 30 s, capture 1 min, heating cycle 1 min, flush 5 min, in that order.
- [ ] `docker compose run --rm --no-deps app python manage.py help run_teplo` prints the command help; CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Write the SQLite signal and its tests**

`app/apps/core/signals.py`:

```python
from typing import Protocol

from django.db.backends.signals import connection_created
from django.dispatch import receiver

SQLITE_PRAGMAS = ('PRAGMA journal_mode=WAL', 'PRAGMA synchronous=NORMAL')


class SqlCursor(Protocol):
    def execute(self, sql: str) -> object: ...

    def close(self) -> None: ...


class SqlConnection(Protocol):
    @property
    def vendor(self) -> str: ...

    def cursor(self) -> SqlCursor: ...


@receiver(connection_created)
def configure_sqlite(connection: SqlConnection, **_kwargs: object) -> None:
    if connection.vendor != 'sqlite':
        return

    cursor = connection.cursor()
    for pragma in SQLITE_PRAGMAS:
        cursor.execute(pragma)
    cursor.close()
```

`app/apps/core/signals_tests.py`:

```python
import pytest

from apps.core.signals import SQLITE_PRAGMAS, configure_sqlite


class CursorStub:
    def __init__(self) -> None:
        self._statements: list[str] = []

    @property
    def statements(self) -> list[str]:
        return self._statements

    def execute(self, sql: str) -> object:
        self._statements.append(sql)
        return None

    def close(self) -> None:
        return None


class ConnectionStub:
    def __init__(self, vendor: str, cursor: CursorStub) -> None:
        self._vendor = vendor
        self._cursor = cursor

    @property
    def vendor(self) -> str:
        return self._vendor

    def cursor(self) -> CursorStub:
        return self._cursor


@pytest.fixture
def f_cursor() -> CursorStub:
    return CursorStub()


@pytest.fixture
def f_sqlite_connection(f_cursor: CursorStub) -> ConnectionStub:
    return ConnectionStub(vendor='sqlite', cursor=f_cursor)


@pytest.fixture
def f_postgres_connection(f_cursor: CursorStub) -> ConnectionStub:
    return ConnectionStub(vendor='postgresql', cursor=f_cursor)


def test_sqlite_connection_gets_pragmas(f_sqlite_connection: ConnectionStub, f_cursor: CursorStub) -> None:
    configure_sqlite(connection=f_sqlite_connection)

    assert f_cursor.statements == list(SQLITE_PRAGMAS)


def test_other_vendors_are_left_alone(f_postgres_connection: ConnectionStub, f_cursor: CursorStub) -> None:
    configure_sqlite(connection=f_postgres_connection)

    assert f_cursor.statements == []
```

`app/apps/core/apps.py`:

```python
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'

    def ready(self) -> None:
        from apps.core import signals  # noqa: F401
```

- [ ] **Step 2: Write the job runner, its tests and the uvicorn server**

`app/apps/core/runtime/__init__.py`: empty file.

`app/apps/core/runtime/periodic.py`:

```python
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

import structlog
from django.db import close_old_connections

logger = structlog.get_logger(__name__)

Action = Callable[[], object]


@dataclass(frozen=True, kw_only=True)
class PeriodicJob:
    name: str
    interval: timedelta
    action: Action


class JobRunner:
    def __init__(self, run_in_thread: Callable[[Action], Awaitable[object]]) -> None:
        self._run_in_thread = run_in_thread

    @classmethod
    def init(cls) -> 'JobRunner':
        return cls(run_in_thread=asyncio.to_thread)

    async def run_forever(self, job: PeriodicJob) -> None:
        while True:
            await self.run_once(job)
            await asyncio.sleep(job.interval.total_seconds())

    async def run_once(self, job: PeriodicJob) -> None:
        try:
            await self._run_in_thread(_with_fresh_connections(job.action))
        except Exception:
            logger.exception('periodic_job_failed', job=job.name)


def _with_fresh_connections(action: Action) -> Action:
    def run() -> object:
        close_old_connections()
        try:
            return action()
        finally:
            close_old_connections()

    return run
```

`app/apps/core/runtime/periodic_tests.py`:

```python
import asyncio
from datetime import timedelta

import pytest

from apps.core.runtime.periodic import Action, JobRunner, PeriodicJob


async def _run_inline(action: Action) -> object:
    return action()


def _explode() -> object:
    raise RuntimeError('boom')


@pytest.fixture
def f_calls() -> list[str]:
    return []


@pytest.fixture
def f_runner() -> JobRunner:
    return JobRunner(run_in_thread=_run_inline)


@pytest.fixture
def f_job(f_calls: list[str]) -> PeriodicJob:
    return PeriodicJob(name='record', interval=timedelta(seconds=1), action=lambda: f_calls.append('ran'))


@pytest.fixture
def f_failing_job() -> PeriodicJob:
    return PeriodicJob(name='explode', interval=timedelta(seconds=1), action=_explode)


def test_run_once_executes_action(f_runner: JobRunner, f_job: PeriodicJob, f_calls: list[str]) -> None:
    asyncio.run(f_runner.run_once(f_job))

    assert f_calls == ['ran']


def test_run_once_survives_failing_action(f_runner: JobRunner, f_failing_job: PeriodicJob) -> None:
    assert asyncio.run(f_runner.run_once(f_failing_job)) is None
```

`app/apps/core/runtime/server.py`:

```python
import uvicorn


async def serve_asgi(host: str, port: int) -> None:
    config = uvicorn.Config(
        app='settings.asgi:application',
        host=host,
        port=port,
        lifespan='off',
        log_config=None,
        access_log=False,
    )
    await uvicorn.Server(config).serve()
```

- [ ] **Step 3: Write the teplo jobs, runtime and command**

`app/apps/system/services/jobs.py`:

```python
from datetime import timedelta

from django.utils import timezone

from apps.climate.usecases.run_heating_cycle import RunHeatingCycleInputDTO, RunHeatingCycleUseCase
from apps.core.runtime.periodic import PeriodicJob
from apps.devices.usecases.poll_thermostats import PollThermostatsInputDTO, PollThermostatsUseCase
from apps.telemetry.usecases.capture_samples import CaptureSamplesInputDTO, CaptureSamplesUseCase
from apps.telemetry.usecases.flush_samples import FlushSamplesInputDTO, FlushSamplesUseCase
from apps.telemetry.usecases.journal_startup import JournalStartupInputDTO, JournalStartupUseCase


def teplo_jobs() -> tuple[PeriodicJob, ...]:
    return (
        PeriodicJob(name='poll_thermostats', interval=timedelta(seconds=30), action=poll_thermostats),
        PeriodicJob(name='capture_samples', interval=timedelta(minutes=1), action=capture_samples),
        PeriodicJob(name='run_heating_cycle', interval=timedelta(minutes=1), action=run_heating_cycle),
        PeriodicJob(name='flush_samples', interval=timedelta(minutes=5), action=flush_samples),
    )


def journal_startup() -> object:
    return JournalStartupUseCase.init().execute(JournalStartupInputDTO(now=timezone.now()))


def poll_thermostats() -> object:
    return PollThermostatsUseCase.init().execute(PollThermostatsInputDTO(now=timezone.now()))


def capture_samples() -> object:
    return CaptureSamplesUseCase.init().execute(CaptureSamplesInputDTO(now=timezone.now()))


def run_heating_cycle() -> object:
    return RunHeatingCycleUseCase.init().execute(RunHeatingCycleInputDTO(now=timezone.now()))


def flush_samples() -> object:
    return FlushSamplesUseCase.init().execute(FlushSamplesInputDTO())
```

`app/apps/system/services/jobs_tests.py`:

```python
from datetime import timedelta

from apps.system.services.jobs import teplo_jobs


def test_teplo_jobs_schedule() -> None:
    assert [(job.name, job.interval) for job in teplo_jobs()] == [
        ('poll_thermostats', timedelta(seconds=30)),
        ('capture_samples', timedelta(minutes=1)),
        ('run_heating_cycle', timedelta(minutes=1)),
        ('flush_samples', timedelta(minutes=5)),
    ]
```

`app/apps/system/services/runtime.py`:

```python
import asyncio
from datetime import timedelta

from apps.core.runtime.periodic import JobRunner, PeriodicJob
from apps.core.runtime.server import serve_asgi
from apps.system.services.jobs import flush_samples, journal_startup, teplo_jobs

ONCE = timedelta(0)


async def run_teplo(host: str, port: int) -> None:
    runner = JobRunner.init()
    await runner.run_once(PeriodicJob(name='journal_startup', interval=ONCE, action=journal_startup))
    tasks = [asyncio.create_task(runner.run_forever(job)) for job in teplo_jobs()]
    try:
        await serve_asgi(host=host, port=port)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runner.run_once(PeriodicJob(name='flush_samples', interval=ONCE, action=flush_samples))
```

`app/apps/system/management/__init__.py` and `app/apps/system/management/commands/__init__.py`: empty files.

`app/apps/system/management/commands/run_teplo.py`:

```python
import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.system.services.runtime import run_teplo


class Command(BaseCommand):
    help = 'Run teplo: control loops and the panel in one process'

    def handle(self, *args: object, **options: object) -> None:
        asyncio.run(run_teplo(host=settings.TEPLO_PANEL_HOST, port=settings.TEPLO_PANEL_PORT))
```

- [ ] **Step 4: Check the command, lint, commit, push, watch CI**

```bash
docker compose run --rm --no-deps app python manage.py help run_teplo
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/apps/core app/apps/system
git commit -m 'Add runtime with periodic jobs and run_teplo command'
git push origin stage-1
sh scripts/ci-watch.sh
```

Expected from `help run_teplo`: the usage line and `Run teplo: control loops and the panel in one process`.

```json:metadata
{"files": ["app/apps/core/signals.py", "app/apps/core/signals_tests.py", "app/apps/core/apps.py", "app/apps/core/runtime/periodic.py", "app/apps/core/runtime/periodic_tests.py", "app/apps/core/runtime/server.py", "app/apps/system/services/jobs.py", "app/apps/system/services/jobs_tests.py", "app/apps/system/services/runtime.py", "app/apps/system/management/commands/run_teplo.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["SQLite connections get WAL and synchronous=NORMAL; other vendors untouched", "run_once runs the action and survives a raising action", "teplo_jobs: poll 30s, capture 1m, heating cycle 1m, flush 5m in order", "manage.py help run_teplo prints the command help; CI green"], "modelTier": "standard"}
```

---

### Task 14: Panel — login, dashboard, boost, journal, API v1, owner account

**Goal:** A password-protected HTMX dashboard with zone cards and a boost button, a journal page, `GET /api/v1/zones/`, and an `ensure_panel_user` command that creates the `owner` superuser from the secrets file.

**Files:**
- Create: `app/conftest.py`, `app/apps/panel/conftest.py`
- Create: `app/apps/panel/services/__init__.py`, `app/apps/panel/services/zone_overview.py`, `app/apps/panel/services/zone_overview_tests.py`
- Create: `app/apps/panel/views/__init__.py`, `dashboard.py`, `dashboard_tests.py`, `zones_partial.py`, `zones_partial_tests.py`, `boost_zone.py`, `boost_zone_tests.py`, `journal.py`, `journal_tests.py` (under `app/apps/panel/views/`)
- Create: `app/apps/panel/views/api/__init__.py`, `app/apps/panel/views/api/v1/__init__.py`, `app/apps/panel/views/api/v1/zones.py`, `app/apps/panel/views/api/v1/zones_tests.py`
- Create: `app/apps/panel/serializers/__init__.py`, `app/apps/panel/serializers/zone_overview.py`
- Create: `app/apps/panel/urls.py`; Modify: `app/settings/urls.py`
- Create: `app/apps/panel/templates/panel/base.html`, `login.html`, `dashboard.html`, `_zones.html`, `journal.html`
- Create: `app/apps/panel/static/panel/panel.css`, `app/apps/panel/static/panel/htmx.min.js` (downloaded)
- Create: `app/apps/system/repositories/__init__.py`, `app/apps/system/repositories/panel_user.py`, `app/apps/system/usecases/__init__.py`, `app/apps/system/usecases/ensure_panel_user.py`, `app/apps/system/usecases/ensure_panel_user_tests.py`, `app/apps/system/management/commands/ensure_panel_user.py`

**Acceptance Criteria:**
- [ ] Anonymous `GET /` redirects to `/login/?next=/`; anonymous `GET /api/v1/zones/` returns 403.
- [ ] A logged-in `GET /` renders zone names from `ZoneOverviewService`; `GET /zones/` renders the partial.
- [ ] `POST /zones/kitchen/boost/` executes `BoostZoneUseCase` with zone `kitchen` and returns the partial; an unknown zone returns 404.
- [ ] `GET /journal/` lists the latest 100 journal entries.
- [ ] `GET /api/v1/zones/` returns the full zone overview JSON.
- [ ] `ZoneOverviewService` combines schedule mode, target setpoint and the latest reading.
- [ ] `ensure_panel_user` creates or updates `owner` as a superuser with the secrets password and raises `SecretMissingError` without one.
- [ ] CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Shared fixtures**

`app/conftest.py`:

```python
import pytest
from django.contrib.auth.models import User
from django.test import Client


@pytest.fixture
def f_owner() -> User:
    return User.objects.create_user(username='owner', password='owner-password')


@pytest.fixture
def f_client(f_owner: User) -> Client:
    client = Client()
    client.force_login(f_owner)
    return client


@pytest.fixture
def f_anonymous_client() -> Client:
    return Client()
```

`app/apps/panel/conftest.py`:

```python
from decimal import Decimal

import pytest

from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.panel.services.zone_overview import PanelOverview, ZoneOverview
from apps.system.domain.clock import ClockStatus


@pytest.fixture
def panel_overview() -> PanelOverview:
    return PanelOverview(
        zones=(
            ZoneOverview(
                slug='kitchen',
                name='Кухня',
                kind=ZoneKind.AIR,
                mode=ScheduleMode.COMFORT,
                available=True,
                error=None,
                floor_temperature=Decimal('24.5'),
                power=True,
                setpoint=Decimal('28.0'),
                target_setpoint=Decimal('28.0'),
                boosted=False,
                live_control=False,
            ),
        ),
        clock=ClockStatus(trusted=True, reason='ntp synchronized'),
        dry_run=True,
    )
```

- [ ] **Step 2: Zone overview service and its test**

`app/apps/panel/services/__init__.py`: empty file.

`app/apps/panel/services/zone_overview.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.climate.domain.floor_policy import decide_floor_target
from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.domain.schedule import resolve_mode
from apps.climate.domain.zone import ZoneSettings
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.services.state_store import ThermostatStateStore, thermostat_state_store
from apps.system.domain.clock import ClockStatus, ClockTrustSource
from apps.system.services.clock_trust import clock_trust_source


@dataclass(frozen=True, kw_only=True)
class ZoneOverview:
    slug: str
    name: str
    kind: ZoneKind
    mode: ScheduleMode
    available: bool
    error: str | None
    floor_temperature: Decimal | None
    power: bool | None
    setpoint: Decimal | None
    target_setpoint: Decimal | None
    boosted: bool
    live_control: bool


@dataclass(frozen=True, kw_only=True)
class PanelOverview:
    zones: tuple[ZoneOverview, ...]
    clock: ClockStatus
    dry_run: bool


class ZoneOverviewService:
    def __init__(
        self,
        zones: ZoneRepository,
        store: ThermostatStateStore,
        clock: ClockTrustSource,
        dry_run: bool,
    ) -> None:
        self._zones = zones
        self._store = store
        self._clock = clock
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'ZoneOverviewService':
        return cls(
            zones=ZoneRepository(),
            store=thermostat_state_store,
            clock=clock_trust_source(),
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def overview(self, now: datetime) -> PanelOverview:
        local_now = timezone.localtime(now)
        clock = self._clock.status()
        return PanelOverview(
            zones=tuple(self._zone(zone, local_now, clock.trusted) for zone in self._zones.all_settings()),
            clock=clock,
            dry_run=self._dry_run,
        )

    def _zone(self, zone: ZoneSettings, now: datetime, clock_trusted: bool) -> ZoneOverview:
        snapshot = self._store.snapshot(zone.thermostat_slug, now)
        mode = resolve_mode(zone.windows, now, zone.preheat_lead)
        target = decide_floor_target(zone, mode, now, clock_trusted=clock_trusted)
        state = snapshot.state
        return ZoneOverview(
            slug=zone.slug,
            name=zone.name,
            kind=zone.kind,
            mode=mode,
            available=snapshot.available,
            error=snapshot.error,
            floor_temperature=state.floor_temperature if state else None,
            power=state.power if state else None,
            setpoint=state.setpoint if state else None,
            target_setpoint=target.setpoint,
            boosted=zone.is_boosted(now),
            live_control=zone.live_control,
        )
```

`app/apps/panel/services/zone_overview_tests.py`:

```python
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.services.state_store import ThermostatStateStore
from apps.panel.services.zone_overview import PanelOverview, ZoneOverview, ZoneOverviewService
from apps.system.domain.clock import ClockStatus
from apps.system.stubs import ClockStub

pytestmark = pytest.mark.django_db

MORNING = datetime(2026, 10, 29, 7, 0, tzinfo=ZoneInfo('Europe/Moscow'))


@pytest.fixture
def f_service() -> ZoneOverviewService:
    zone = ZoneFactory(slug='kitchen', name='Кухня', thermostat__slug='thermostat-kitchen')
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_state(
        'thermostat-kitchen',
        ThermostatState(
            power=True,
            mode=ThermostatMode.HOLD,
            setpoint=Decimal('22.0'),
            floor_temperature=Decimal('21.5'),
            read_at=MORNING,
        ),
    )
    return ZoneOverviewService(zones=ZoneRepository(), store=store, clock=ClockStub(trusted=True), dry_run=True)


def test_overview_combines_schedule_and_readings(f_service: ZoneOverviewService) -> None:
    assert f_service.overview(MORNING) == PanelOverview(
        zones=(
            ZoneOverview(
                slug='kitchen',
                name='Кухня',
                kind=ZoneKind.AIR,
                mode=ScheduleMode.COMFORT,
                available=True,
                error=None,
                floor_temperature=Decimal('21.5'),
                power=True,
                setpoint=Decimal('22.0'),
                target_setpoint=Decimal('28.0'),
                boosted=False,
                live_control=False,
            ),
        ),
        clock=ClockStatus(trusted=True, reason='stub'),
        dry_run=True,
    )
```

- [ ] **Step 3: Views, serializer and URLs**

`app/apps/panel/views/__init__.py`, `app/apps/panel/views/api/__init__.py`, `app/apps/panel/views/api/v1/__init__.py`, `app/apps/panel/serializers/__init__.py`: empty files.

`app/apps/panel/views/dashboard.py`:

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views.generic import TemplateView

from apps.panel.services.zone_overview import ZoneOverviewService


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'panel/dashboard.html'

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context['overview'] = ZoneOverviewService.init().overview(timezone.now())
        return context
```

`app/apps/panel/views/zones_partial.py`:

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views.generic import TemplateView

from apps.panel.services.zone_overview import ZoneOverviewService


class ZonesPartialView(LoginRequiredMixin, TemplateView):
    template_name = 'panel/_zones.html'

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context['overview'] = ZoneOverviewService.init().overview(timezone.now())
        return context
```

`app/apps/panel/views/boost_zone.py`:

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.usecases.boost_zone import BoostZoneInputDTO, BoostZoneUseCase
from apps.panel.services.zone_overview import ZoneOverviewService


class BoostZoneView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, slug: str) -> HttpResponse:
        try:
            BoostZoneUseCase.init().execute(BoostZoneInputDTO(zone=slug, now=timezone.now()))
        except ZoneNotFoundError as error:
            raise Http404(error.detail) from error

        overview = ZoneOverviewService.init().overview(timezone.now())
        return render(request, 'panel/_zones.html', {'overview': overview})
```

`app/apps/panel/views/journal.py`:

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from apps.telemetry.repositories.journal import JournalRepository

JOURNAL_LIMIT = 100


class JournalView(LoginRequiredMixin, TemplateView):
    template_name = 'panel/journal.html'

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context['entries'] = JournalRepository.latest(limit=JOURNAL_LIMIT)
        return context
```

`app/apps/panel/serializers/zone_overview.py`:

```python
from rest_framework import serializers


class ZoneOverviewSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    kind = serializers.CharField()
    mode = serializers.CharField()
    available = serializers.BooleanField()
    error = serializers.CharField(allow_null=True)
    floor_temperature = serializers.DecimalField(max_digits=4, decimal_places=1, allow_null=True)
    power = serializers.BooleanField(allow_null=True)
    setpoint = serializers.DecimalField(max_digits=4, decimal_places=1, allow_null=True)
    target_setpoint = serializers.DecimalField(max_digits=4, decimal_places=1, allow_null=True)
    boosted = serializers.BooleanField()
    live_control = serializers.BooleanField()
```

`app/apps/panel/views/api/v1/zones.py`:

```python
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.panel.serializers.zone_overview import ZoneOverviewSerializer
from apps.panel.services.zone_overview import ZoneOverviewService


class ZonesApiView(APIView):
    def get(self, _request: Request) -> Response:
        overview = ZoneOverviewService.init().overview(timezone.now())
        return Response(ZoneOverviewSerializer(overview.zones, many=True).data)
```

`app/apps/panel/urls.py`:

```python
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from apps.panel.views.api.v1.zones import ZonesApiView
from apps.panel.views.boost_zone import BoostZoneView
from apps.panel.views.dashboard import DashboardView
from apps.panel.views.journal import JournalView
from apps.panel.views.zones_partial import ZonesPartialView

app_name = 'panel'

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('zones/', ZonesPartialView.as_view(), name='zones'),
    path('zones/<slug:slug>/boost/', BoostZoneView.as_view(), name='boost-zone'),
    path('journal/', JournalView.as_view(), name='journal'),
    path('login/', LoginView.as_view(template_name='panel/login.html'), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('api/v1/zones/', ZonesApiView.as_view(), name='api-v1-zones'),
]
```

`app/settings/urls.py`:

```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.panel.urls')),
]
```

- [ ] **Step 4: Templates and static files**

`app/apps/panel/templates/panel/base.html`:

```html
{% load static %}<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>teplo</title>
  <link rel="stylesheet" href="{% static 'panel/panel.css' %}">
  <script src="{% static 'panel/htmx.min.js' %}" defer></script>
</head>
<body hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'>
  <header>
    <a href="{% url 'panel:dashboard' %}">teplo</a>
    {% if user.is_authenticated %}
    <nav>
      <a href="{% url 'panel:journal' %}">Journal</a>
      <a href="{% url 'admin:index' %}">Settings</a>
      <form method="post" action="{% url 'panel:logout' %}">{% csrf_token %}<button type="submit">Log out</button></form>
    </nav>
    {% endif %}
  </header>
  <main>{% block content %}{% endblock %}</main>
</body>
</html>
```

`app/apps/panel/templates/panel/login.html`:

```html
{% extends 'panel/base.html' %}
{% block content %}
<form method="post" class="login">
  {% csrf_token %}
  {{ form.as_p }}
  <button type="submit">Log in</button>
</form>
{% endblock %}
```

`app/apps/panel/templates/panel/dashboard.html`:

```html
{% extends 'panel/base.html' %}
{% block content %}
<section id="zones" hx-get="{% url 'panel:zones' %}" hx-trigger="every 10s" hx-swap="innerHTML">
  {% include 'panel/_zones.html' %}
</section>
{% endblock %}
```

`app/apps/panel/templates/panel/_zones.html`:

```html
{% if overview.dry_run %}<p class="banner">Dry run: commands are journaled, thermostats are not changed.</p>{% endif %}
{% if not overview.clock.trusted %}<p class="banner warning">Clock not trusted ({{ overview.clock.reason }}): holding comfort setpoints.</p>{% endif %}
<div class="zones">
  {% for zone in overview.zones %}
  <article class="zone{% if not zone.available %} offline{% endif %}">
    <h2>{{ zone.name }}</h2>
    <p class="mode">{{ zone.mode }}{% if zone.boosted %} · boost{% endif %}{% if not zone.live_control %} · observe{% endif %}</p>
    <dl>
      <dt>Floor</dt><dd>{% if zone.floor_temperature is not None %}{{ zone.floor_temperature }} °C{% else %}—{% endif %}</dd>
      <dt>Setpoint</dt><dd>{% if zone.setpoint is not None %}{{ zone.setpoint }} °C{% else %}—{% endif %}</dd>
      <dt>Target</dt><dd>{% if zone.target_setpoint is not None %}{{ zone.target_setpoint }} °C{% else %}off{% endif %}</dd>
    </dl>
    {% if zone.error %}<p class="error">{{ zone.error }}</p>{% endif %}
    <button hx-post="{% url 'panel:boost-zone' zone.slug %}" hx-target="#zones" hx-swap="innerHTML">Boost 2 h</button>
  </article>
  {% endfor %}
</div>
```

`app/apps/panel/templates/panel/journal.html`:

```html
{% extends 'panel/base.html' %}
{% block content %}
<table class="journal">
  <thead><tr><th>Time</th><th>Event</th><th>Subject</th><th>Details</th></tr></thead>
  <tbody>
  {% for entry in entries %}
    <tr><td>{{ entry.created_at|date:'d.m H:i:s' }}</td><td>{{ entry.kind }}</td><td>{{ entry.subject }}</td><td><code>{{ entry.payload }}</code></td></tr>
  {% empty %}
    <tr><td colspan="4">No events yet.</td></tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

`app/apps/panel/static/panel/panel.css`:

```css
:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
body { margin: 0 auto; max-width: 960px; padding: 1rem; }
header { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
header nav { display: flex; gap: 1rem; align-items: center; }
header form { margin: 0; }
.zones { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 1rem; }
.zone { border: 1px solid #8884; border-radius: 12px; padding: 1rem; }
.zone.offline { opacity: 0.6; }
.zone dl { display: grid; grid-template-columns: auto 1fr; gap: 0.25rem 0.75rem; }
.zone dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
.mode { color: #888; margin-top: -0.5rem; }
.banner { padding: 0.5rem 1rem; border-radius: 8px; background: #8882; }
.banner.warning, .error { color: #c62828; }
.journal { width: 100%; border-collapse: collapse; }
.journal td, .journal th { border-bottom: 1px solid #8883; padding: 0.25rem 0.5rem; text-align: left; vertical-align: top; }
button { padding: 0.4rem 0.8rem; border-radius: 8px; border: 1px solid #8886; background: transparent; cursor: pointer; }
```

Download HTMX so the panel needs no internet:

```bash
curl -sSfL https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js -o app/apps/panel/static/panel/htmx.min.js
head -c 60 app/apps/panel/static/panel/htmx.min.js
```

Expected: a minified JavaScript header, not an HTML error page.

- [ ] **Step 5: View tests (mocks only)**

`app/apps/panel/views/dashboard_tests.py`:

```python
from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.services.zone_overview import PanelOverview

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_overview_service(panel_overview: PanelOverview) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.dashboard.ZoneOverviewService') as service:
        service.init.return_value.overview.return_value = panel_overview
        yield service


def test_dashboard_lists_zones(f_client: Client, m_overview_service: MagicMock) -> None:
    response = f_client.get('/')

    assert (response.status_code, 'Кухня' in response.content.decode(), m_overview_service.init.call_count) == (
        200,
        True,
        1,
    )


def test_dashboard_redirects_anonymous_to_login(f_anonymous_client: Client) -> None:
    response = f_anonymous_client.get('/')

    assert (response.status_code, response['Location']) == (302, '/login/?next=/')
```

`app/apps/panel/views/zones_partial_tests.py`:

```python
from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.services.zone_overview import PanelOverview

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_overview_service(panel_overview: PanelOverview) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.zones_partial.ZoneOverviewService') as service:
        service.init.return_value.overview.return_value = panel_overview
        yield service


def test_zones_partial_renders_cards(f_client: Client, m_overview_service: MagicMock) -> None:
    response = f_client.get('/zones/')

    content = response.content.decode()
    assert (response.status_code, 'Кухня' in content, '<html' in content, m_overview_service.init.call_count) == (
        200,
        True,
        False,
        1,
    )
```

`app/apps/panel/views/boost_zone_tests.py`:

```python
from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.climate.domain.errors import ZoneNotFoundError
from apps.panel.services.zone_overview import PanelOverview

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_boost_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.boost_zone.BoostZoneUseCase') as use_case:
        yield use_case


@pytest.fixture
def m_missing_zone_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.boost_zone.BoostZoneUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = ZoneNotFoundError('attic')
        yield use_case


@pytest.fixture
def m_overview_service(panel_overview: PanelOverview) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.boost_zone.ZoneOverviewService') as service:
        service.init.return_value.overview.return_value = panel_overview
        yield service


def test_boost_executes_use_case_and_renders_zones(
    f_client: Client,
    m_boost_use_case: MagicMock,
    m_overview_service: MagicMock,
) -> None:
    response = f_client.post('/zones/kitchen/boost/')

    executed_input = m_boost_use_case.init.return_value.execute.call_args.args[0]
    assert (
        response.status_code,
        executed_input.zone,
        'Кухня' in response.content.decode(),
        m_overview_service.init.call_count,
    ) == (200, 'kitchen', True, 1)


def test_boost_unknown_zone_returns_404(
    f_client: Client,
    m_missing_zone_use_case: MagicMock,
    m_overview_service: MagicMock,
) -> None:
    response = f_client.post('/zones/attic/boost/')

    assert (response.status_code, m_missing_zone_use_case.init.call_count, m_overview_service.init.call_count) == (
        404,
        1,
        0,
    )
```

`app/apps/panel/views/journal_tests.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.telemetry.models import JournalEntry

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_journal_repository() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal.JournalRepository') as repository:
        repository.latest.return_value = [
            JournalEntry(
                kind='zone_boosted',
                subject='kitchen',
                payload={'until': '2026-10-29T08:00:00Z'},
                created_at=datetime(2026, 10, 29, 6, 0, tzinfo=UTC),
            ),
        ]
        yield repository


def test_journal_lists_latest_entries(f_client: Client, m_journal_repository: MagicMock) -> None:
    response = f_client.get('/journal/')

    assert (
        response.status_code,
        'zone_boosted' in response.content.decode(),
        m_journal_repository.latest.call_args.kwargs,
    ) == (200, True, {'limit': 100})
```

`app/apps/panel/views/api/v1/zones_tests.py`:

```python
from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.services.zone_overview import PanelOverview

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_overview_service(panel_overview: PanelOverview) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.zones.ZoneOverviewService') as service:
        service.init.return_value.overview.return_value = panel_overview
        yield service


def test_zones_api_returns_overview(f_client: Client, m_overview_service: MagicMock) -> None:
    response = f_client.get('/api/v1/zones/')

    assert (response.status_code, response.json(), m_overview_service.init.call_count) == (
        200,
        [
            {
                'slug': 'kitchen',
                'name': 'Кухня',
                'kind': 'air',
                'mode': 'comfort',
                'available': True,
                'error': None,
                'floor_temperature': '24.5',
                'power': True,
                'setpoint': '28.0',
                'target_setpoint': '28.0',
                'boosted': False,
                'live_control': False,
            },
        ],
        1,
    )


def test_zones_api_requires_login(f_anonymous_client: Client) -> None:
    assert f_anonymous_client.get('/api/v1/zones/').status_code == 403
```

- [ ] **Step 6: Owner account use case, repository, command and tests**

`app/apps/system/repositories/__init__.py`, `app/apps/system/usecases/__init__.py`: empty files.

`app/apps/system/repositories/panel_user.py`:

```python
from django.contrib.auth.models import User


class PanelUserRepository:
    @staticmethod
    def upsert_superuser(username: str, password: str) -> bool:
        user, created = User.objects.get_or_create(username=username)
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()
        return created
```

`app/apps/system/usecases/ensure_panel_user.py`:

```python
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from apps.core.domain.errors import SecretMissingError
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.secrets import SecretsFile
from apps.system.repositories.panel_user import PanelUserRepository


@dataclass(frozen=True, kw_only=True)
class EnsurePanelUserInputDTO:
    username: str


@dataclass(frozen=True, kw_only=True)
class EnsurePanelUserOutputDTO:
    username: str
    created: bool


class EnsurePanelUserUseCase(BaseUseCase[EnsurePanelUserInputDTO, EnsurePanelUserOutputDTO]):
    def __init__(self, secrets: SecretsFile, users: PanelUserRepository) -> None:
        super().__init__()
        self._secrets = secrets
        self._users = users

    @classmethod
    def init(cls) -> 'EnsurePanelUserUseCase':
        return cls(secrets=SecretsFile.init(Path(settings.TEPLO_SECRETS_FILE)), users=PanelUserRepository())

    def _execute(self, input_dto: EnsurePanelUserInputDTO) -> EnsurePanelUserOutputDTO:
        password = self._secrets.panel_password
        if not password:
            raise SecretMissingError('panel.password')

        created = self._users.upsert_superuser(input_dto.username, password)
        return EnsurePanelUserOutputDTO(username=input_dto.username, created=created)
```

`app/apps/system/usecases/ensure_panel_user_tests.py`:

```python
import pytest
from django.contrib.auth.models import User

from apps.core.domain.errors import SecretMissingError
from apps.core.services.secrets import SecretsFile
from apps.system.repositories.panel_user import PanelUserRepository
from apps.system.usecases.ensure_panel_user import (
    EnsurePanelUserInputDTO,
    EnsurePanelUserOutputDTO,
    EnsurePanelUserUseCase,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def f_use_case() -> EnsurePanelUserUseCase:
    secrets = SecretsFile(devices={}, panel_password='panel-secret')
    return EnsurePanelUserUseCase(secrets=secrets, users=PanelUserRepository())


@pytest.fixture
def f_no_password_use_case() -> EnsurePanelUserUseCase:
    return EnsurePanelUserUseCase(secrets=SecretsFile(devices={}, panel_password=None), users=PanelUserRepository())


def test_creates_owner_superuser(f_use_case: EnsurePanelUserUseCase) -> None:
    output = f_use_case.execute(EnsurePanelUserInputDTO(username='owner'))

    owner = User.objects.get(username='owner')
    assert (output, owner.check_password('panel-secret'), owner.is_superuser, owner.is_staff) == (
        EnsurePanelUserOutputDTO(username='owner', created=True),
        True,
        True,
        True,
    )


def test_updates_existing_owner(f_use_case: EnsurePanelUserUseCase, f_owner: User) -> None:
    output = f_use_case.execute(EnsurePanelUserInputDTO(username=f_owner.username))

    assert (output.created, User.objects.get(pk=f_owner.pk).check_password('panel-secret')) == (False, True)


def test_missing_password_is_rejected(f_no_password_use_case: EnsurePanelUserUseCase) -> None:
    with pytest.raises(SecretMissingError):
        f_no_password_use_case.execute(EnsurePanelUserInputDTO(username='owner'))
```

`app/apps/system/management/commands/ensure_panel_user.py`:

```python
from django.core.management.base import BaseCommand

from apps.system.usecases.ensure_panel_user import EnsurePanelUserInputDTO, EnsurePanelUserUseCase

OWNER_USERNAME = 'owner'


class Command(BaseCommand):
    help = 'Create or update the panel owner account from the secrets file'

    def handle(self, *args: object, **options: object) -> None:
        output = EnsurePanelUserUseCase.init().execute(EnsurePanelUserInputDTO(username=OWNER_USERNAME))
        self.stdout.write(f'panel user {output.username} {"created" if output.created else "updated"}')
```

- [ ] **Step 7: Lint, commit, push, watch CI**

```bash
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/conftest.py app/apps/panel app/apps/system app/settings/urls.py
git commit -m 'Add panel dashboard, journal, API v1 and owner account'
git push origin stage-1
sh scripts/ci-watch.sh
```

```json:metadata
{"files": ["app/conftest.py", "app/apps/panel/conftest.py", "app/apps/panel/services/zone_overview.py", "app/apps/panel/services/zone_overview_tests.py", "app/apps/panel/views/dashboard.py", "app/apps/panel/views/zones_partial.py", "app/apps/panel/views/boost_zone.py", "app/apps/panel/views/journal.py", "app/apps/panel/views/api/v1/zones.py", "app/apps/panel/serializers/zone_overview.py", "app/apps/panel/urls.py", "app/settings/urls.py", "app/apps/panel/templates/panel/base.html", "app/apps/panel/templates/panel/_zones.html", "app/apps/panel/static/panel/htmx.min.js", "app/apps/system/usecases/ensure_panel_user.py", "app/apps/system/repositories/panel_user.py", "app/apps/system/management/commands/ensure_panel_user.py"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["anonymous GET / redirects to /login/?next=/; anonymous API returns 403", "logged-in dashboard and partial render zone names from ZoneOverviewService", "POST /zones/kitchen/boost/ executes BoostZoneUseCase with zone kitchen; unknown zone 404", "journal page lists latest 100 entries", "API v1 returns the full zone overview JSON", "ZoneOverviewService combines mode, target and reading", "ensure_panel_user creates or updates owner superuser; raises SecretMissingError without password", "CI green"], "modelTier": "standard"}
```

---

### Task 15: Site apps — example and barsoom

**Goal:** Two site apps whose data migrations describe a house: `sites.example` as the template and `sites.barsoom` with the five real thermostats and zones; CI applies both on fresh databases.

**Files:**
- Create: `app/sites/example/__init__.py`, `app/sites/example/apps.py`, `app/sites/example/migrations/__init__.py`, `app/sites/example/migrations/0001_initial_site.py`
- Create: `app/sites/barsoom/__init__.py`, `app/sites/barsoom/apps.py`, `app/sites/barsoom/migrations/__init__.py`, `app/sites/barsoom/migrations/0001_initial_site.py`
- Modify: `.github/workflows/ci.yml`

**Acceptance Criteria:**
- [ ] `TEPLO_SITE=barsoom manage.py migrate` on a fresh database creates 5 thermostats, 5 zones (3 air, 2 floor) with the design setpoints and 35 comfort periods 06:30–23:30.
- [ ] `TEPLO_SITE=example` migrates cleanly.
- [ ] The CI step "Site migrations apply" succeeds; CI green.

**Verify:** `git push origin stage-1 && sh scripts/ci-watch.sh` → success, including "Site migrations apply".

**Steps:**

- [ ] **Step 1: Write the barsoom site**

`app/sites/barsoom/__init__.py` and `app/sites/barsoom/migrations/__init__.py`: empty files.

`app/sites/barsoom/apps.py`:

```python
from django.apps import AppConfig


class BarsoomSiteConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sites.barsoom'
    label = 'site_barsoom'
```

`app/sites/barsoom/migrations/0001_initial_site.py`:

```python
from datetime import time
from decimal import Decimal

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

THERMOSTATS = (
    ('thermostat-living-room', 'Гостиная'),
    ('thermostat-kitchen', 'Кухня'),
    ('thermostat-hallway', 'Теплый пол прихожая'),
    ('thermostat-bathroom-1f', 'Су 1 этаж'),
    ('thermostat-bathroom-2f', 'С/у 2 этаж'),
)
ZONES = (
    ('living-room', 'Гостиная', 'air', 'thermostat-living-room', '28.0', '22.0', '30.0'),
    ('kitchen', 'Кухня', 'air', 'thermostat-kitchen', '28.0', '22.0', '30.0'),
    ('hallway', 'Прихожая', 'air', 'thermostat-hallway', '28.0', '22.0', '30.0'),
    ('bathroom-1f', 'Санузел 1 этаж', 'floor', 'thermostat-bathroom-1f', '18.0', '18.0', '30.0'),
    ('bathroom-2f', 'Санузел 2 этаж', 'floor', 'thermostat-bathroom-2f', '30.0', '22.0', '30.0'),
)
COMFORT_START = time(6, 30)
COMFORT_END = time(23, 30)


def create_site(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    device_model = apps.get_model('devices', 'Device')
    zone_model = apps.get_model('climate', 'Zone')
    period_model = apps.get_model('climate', 'ComfortPeriod')
    devices = {
        slug: device_model.objects.create(slug=slug, name=name, kind='thermostat', driver='tuya_thermostat')
        for slug, name in THERMOSTATS
    }
    for slug, name, kind, thermostat, comfort, night, boost in ZONES:
        zone = zone_model.objects.create(
            slug=slug,
            name=name,
            kind=kind,
            thermostat=devices[thermostat],
            comfort_floor_setpoint=Decimal(comfort),
            night_floor_setpoint=Decimal(night),
            boost_floor_setpoint=Decimal(boost),
        )
        period_model.objects.bulk_create(
            [period_model(zone=zone, weekday=weekday, start=COMFORT_START, end=COMFORT_END) for weekday in range(7)],
        )


def delete_site(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('climate', 'Zone').objects.filter(slug__in=[zone[0] for zone in ZONES]).delete()
    apps.get_model('devices', 'Device').objects.filter(slug__in=[device[0] for device in THERMOSTATS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('devices', '0001_initial'),
        ('climate', '0001_initial'),
    ]

    operations = [migrations.RunPython(create_site, delete_site)]
```

- [ ] **Step 2: Write the example site**

`app/sites/example/__init__.py` and `app/sites/example/migrations/__init__.py`: empty files.

`app/sites/example/apps.py`:

```python
from django.apps import AppConfig


class ExampleSiteConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sites.example'
    label = 'site_example'
```

`app/sites/example/migrations/0001_initial_site.py`:

```python
from datetime import time
from decimal import Decimal

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

THERMOSTATS = (
    ('thermostat-living-room', 'Living room thermostat'),
    ('thermostat-bathroom', 'Bathroom thermostat'),
)
ZONES = (
    ('living-room', 'Living room', 'air', 'thermostat-living-room', '28.0', '22.0', '30.0'),
    ('bathroom', 'Bathroom', 'floor', 'thermostat-bathroom', '30.0', '22.0', '30.0'),
)
WEEKDAY_COMFORT = (time(6, 30), time(23, 0))
WEEKEND_COMFORT = (time(8, 0), time(23, 30))


def create_site(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    device_model = apps.get_model('devices', 'Device')
    zone_model = apps.get_model('climate', 'Zone')
    period_model = apps.get_model('climate', 'ComfortPeriod')
    devices = {
        slug: device_model.objects.create(slug=slug, name=name, kind='thermostat', driver='tuya_thermostat')
        for slug, name in THERMOSTATS
    }
    for slug, name, kind, thermostat, comfort, night, boost in ZONES:
        zone = zone_model.objects.create(
            slug=slug,
            name=name,
            kind=kind,
            thermostat=devices[thermostat],
            comfort_floor_setpoint=Decimal(comfort),
            night_floor_setpoint=Decimal(night),
            boost_floor_setpoint=Decimal(boost),
        )
        period_model.objects.bulk_create(
            [
                period_model(
                    zone=zone,
                    weekday=weekday,
                    start=(WEEKEND_COMFORT if weekday >= 5 else WEEKDAY_COMFORT)[0],
                    end=(WEEKEND_COMFORT if weekday >= 5 else WEEKDAY_COMFORT)[1],
                )
                for weekday in range(7)
            ],
        )


def delete_site(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('climate', 'Zone').objects.filter(slug__in=[zone[0] for zone in ZONES]).delete()
    apps.get_model('devices', 'Device').objects.filter(slug__in=[device[0] for device in THERMOSTATS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('devices', '0001_initial'),
        ('climate', '0001_initial'),
    ]

    operations = [migrations.RunPython(create_site, delete_site)]
```

- [ ] **Step 3: Apply site migrations in CI**

Append to the `steps` of `.github/workflows/ci.yml`:

```yaml
      - name: Site migrations apply
        env:
          DJANGO_SETTINGS_MODULE: settings.settings
        run: |
          for site in example barsoom; do
            TEPLO_SITE=$site TEPLO_DB_PATH=/tmp/teplo-$site.sqlite3 poetry run python app/manage.py migrate --noinput
          done
          TEPLO_SITE=barsoom TEPLO_DB_PATH=/tmp/teplo-barsoom.sqlite3 poetry run python app/manage.py shell -c "from apps.climate.models import ComfortPeriod, Zone; from apps.devices.models import Device; assert (Device.objects.count(), Zone.objects.count(), Zone.objects.filter(kind='air').count(), ComfortPeriod.objects.count()) == (5, 5, 3, 35)"
```

- [ ] **Step 4: Check locally, commit, push, watch CI**

```bash
docker compose run --rm --no-deps -e TEPLO_SITE=barsoom -e TEPLO_DB_PATH=/tmp/barsoom.sqlite3 app python manage.py migrate --noinput
docker compose run --rm --no-deps app sh -c 'cd /code && ruff check . && ruff format --check . && mypy app'
git add app/sites .github/workflows/ci.yml
git commit -m 'Add example and barsoom site apps'
git push origin stage-1
sh scripts/ci-watch.sh
```

Expected from migrate: `Applying site_barsoom.0001_initial_site... OK`.

```json:metadata
{"files": ["app/sites/barsoom/apps.py", "app/sites/barsoom/migrations/0001_initial_site.py", "app/sites/example/apps.py", "app/sites/example/migrations/0001_initial_site.py", ".github/workflows/ci.yml"], "verifyCommand": "git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["barsoom migrate creates 5 thermostats, 5 zones (3 air, 2 floor) and 35 comfort periods", "example migrates cleanly", "CI step Site migrations apply succeeds; CI green"], "modelTier": "mechanical"}
```

---

### Task 16: Router deployment scripts and docs

**Goal:** Scripts to prepare the router once (Entware packages, virtualenv, init script, cron watchdog), deploy a release over SSH, keep teplo running under 300 MB RSS with a bounded log, plus `docs/deploy.md` and a pointer in `CLAUDE.md`.

**Files:**
- Create: `deploy/router/install.sh`, `deploy/router/S90teplo`, `deploy/router/teplo-watchdog.sh`, `deploy/router/teplo.env.example`, `deploy/router/secrets.toml.example`
- Create: `deploy/deploy.sh`
- Create: `docs/deploy.md`
- Modify: `CLAUDE.md`

**Acceptance Criteria:**
- [ ] `sh -n` passes for every script.
- [ ] `deploy.sh` exports requirements without dev dependencies and without `cryptography`, `cffi` and `pycparser`, ships `app` and `deploy` from `HEAD`, installs with `--no-deps`, migrates, collects static files, ensures the owner account, switches `current` and restarts the service.
- [ ] The watchdog restarts teplo when the PID is gone or RSS exceeds 307200 kB, logs restarts, and trims the log above 5 MB without replacing the file.
- [ ] `docs/deploy.md` covers first install, secrets, deploy, enabling live control and rollback; `CLAUDE.md` points to it.
- [ ] CI green.

**Verify:** `for f in deploy/deploy.sh deploy/router/install.sh deploy/router/S90teplo deploy/router/teplo-watchdog.sh; do sh -n "$f" && echo "$f ok"; done` → four `ok` lines; then `git push origin stage-1 && sh scripts/ci-watch.sh` → success.

**Steps:**

- [ ] **Step 1: Router-side scripts**

`deploy/router/install.sh`:

```sh
#!/bin/sh
set -eu

ROOT=/opt/share/teplo
HERE=$(cd "$(dirname "$0")" && pwd)

opkg update
opkg install python3 python3-pip python3-sqlite3 python3-asyncio python3-cryptography
mkdir -p "$ROOT/releases" /opt/etc/teplo /opt/var/lib/teplo/static /opt/var/log/teplo /opt/var/run
[ -x "$ROOT/venv/bin/python" ] || python3 -m venv --system-site-packages --without-pip "$ROOT/venv"
cp "$HERE/S90teplo" /opt/etc/init.d/S90teplo
chmod 755 /opt/etc/init.d/S90teplo
cp "$HERE/teplo-watchdog.sh" "$ROOT/teplo-watchdog.sh"
chmod 755 "$ROOT/teplo-watchdog.sh"
if [ ! -f /opt/etc/teplo/teplo.env ]; then
    cp "$HERE/teplo.env.example" /opt/etc/teplo/teplo.env
    chmod 600 /opt/etc/teplo/teplo.env
fi
if ! grep -q teplo-watchdog /opt/etc/crontabs/root; then
    printf '\n# teplo watchdog: restart when stopped or above 300 MB RSS\n* * * * * %s/teplo-watchdog.sh >/dev/null 2>&1\n' "$ROOT" >> /opt/etc/crontabs/root
fi
echo 'teplo prerequisites installed'
```

`deploy/router/S90teplo`:

```sh
#!/bin/sh

ENV_FILE=/opt/etc/teplo/teplo.env
PIDFILE=/opt/var/run/teplo.pid
LOGFILE=/opt/var/log/teplo/teplo.log
PYTHON=/opt/share/teplo/venv/bin/python
MANAGE=/opt/share/teplo/current/app/manage.py

load_env() {
    set -a
    . "$ENV_FILE"
    set +a
}

start() {
    load_env
    start-stop-daemon -S -b -m -p "$PIDFILE" -O "$LOGFILE" -x /opt/bin/nice -- -n 10 "$PYTHON" "$MANAGE" run_teplo
}

stop() {
    start-stop-daemon -K -p "$PIDFILE" -s TERM
    rm -f "$PIDFILE"
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) stop; sleep 2; start ;;
    status) if start-stop-daemon -K -t -p "$PIDFILE" >/dev/null 2>&1; then echo running; else echo stopped; fi ;;
    *) echo "usage: $0 {start|stop|restart|status}" >&2; exit 1 ;;
esac
```

`deploy/router/teplo-watchdog.sh`:

```sh
#!/bin/sh

PIDFILE=/opt/var/run/teplo.pid
LOGFILE=/opt/var/log/teplo/teplo.log
RESTARTS=/opt/var/log/teplo/restarts.log
MAX_RSS_KB=307200
MAX_LOG_BYTES=5242880

restart() {
    echo "$(date '+%Y-%m-%dT%H:%M:%S') $1" >> "$RESTARTS"
    /opt/etc/init.d/S90teplo restart
}

if [ ! -f "$PIDFILE" ] || ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    restart 'not running'
    exit 0
fi

rss=$(awk '/VmRSS/ {print $2}' "/proc/$(cat "$PIDFILE")/status")
if [ "${rss:-0}" -gt "$MAX_RSS_KB" ]; then
    restart "rss ${rss} kB"
fi

if [ -f "$LOGFILE" ] && [ "$(wc -c < "$LOGFILE")" -gt "$MAX_LOG_BYTES" ]; then
    tail -c 1048576 "$LOGFILE" > "$LOGFILE.1"
    : > "$LOGFILE"
fi
```

`deploy/router/teplo.env.example`:

```
DJANGO_SETTINGS_MODULE=settings.settings
DJANGO_SECRET_KEY=replace-with-a-long-random-string
TEPLO_SITE=barsoom
TEPLO_DB_PATH=/opt/var/lib/teplo/teplo.sqlite3
TEPLO_STATIC_ROOT=/opt/var/lib/teplo/static
TEPLO_SECRETS_FILE=/opt/etc/teplo/secrets.toml
TEPLO_CLOCK_SOURCE=ndms
TEPLO_DRY_RUN=true
TEPLO_PANEL_HOST=192.168.1.1
TEPLO_PANEL_PORT=8088
TEPLO_ALLOWED_HOSTS=192.168.1.1
```

`deploy/router/secrets.toml.example`:

```toml
[devices.thermostat-kitchen]
address = '192.168.1.10'
device_id = 'tuya-device-id'
local_key = 'tuya-local-key'
protocol = '3.3'

[panel]
password = 'choose-a-panel-password'
```

- [ ] **Step 2: Workstation deploy script**

`deploy/deploy.sh`:

```sh
#!/bin/sh
set -eu

HOST=${TEPLO_HOST:-192.168.1.1}
PORT=${TEPLO_SSH_PORT:-222}
ROOT=/opt/share/teplo
SHA=$(git rev-parse --short HEAD)
RELEASE=$ROOT/releases/$SHA

cd "$(dirname "$0")/.."
docker compose run --rm --no-deps -T app sh -c \
    'cd /code && poetry export --without dev --without-hashes --format requirements.txt' \
    | grep -v -E '^(cryptography|cffi|pycparser)==' > requirements.txt
ssh -p "$PORT" "root@$HOST" "mkdir -p $RELEASE"
git archive --format=tar HEAD app deploy | ssh -p "$PORT" "root@$HOST" "tar -x -C $RELEASE"
ssh -p "$PORT" "root@$HOST" "cat > $RELEASE/requirements.txt" < requirements.txt
ssh -p "$PORT" "root@$HOST" "set -eu
cd $RELEASE
$ROOT/venv/bin/python -m pip install --no-cache-dir --no-deps -r requirements.txt
set -a; . /opt/etc/teplo/teplo.env; set +a
$ROOT/venv/bin/python app/manage.py migrate --noinput
$ROOT/venv/bin/python app/manage.py collectstatic --noinput
$ROOT/venv/bin/python app/manage.py ensure_panel_user
ln -sfn $RELEASE $ROOT/current
/opt/etc/init.d/S90teplo restart"
echo "deployed $SHA"
```

- [ ] **Step 3: Write `docs/deploy.md`**

````markdown
# Deploying teplo to the router

Target: Keenetic/Netcraze with Entware on USB storage, SSH to Entware on port 222.

## First install

1. Copy the router scripts and run the installer:

   ```sh
   scp -P 222 -r deploy/router root@192.168.1.1:/tmp/teplo-router
   ssh -p 222 root@192.168.1.1 sh /tmp/teplo-router/install.sh
   ```

2. Edit `/opt/etc/teplo/teplo.env`: set `DJANGO_SECRET_KEY`, `TEPLO_SITE`, `TEPLO_PANEL_HOST`, `TEPLO_ALLOWED_HOSTS`. Keep `TEPLO_DRY_RUN=true` for the first run.
3. Create `/opt/etc/teplo/secrets.toml` from `deploy/router/secrets.toml.example`, one `[devices.<slug>]` table per device slug of the site, and `chmod 600` it. Local keys come from the Smart Life QR login described in `docs/design.md`.

## Deploy a release

```sh
sh deploy/deploy.sh
```

The script installs pinned requirements into `/opt/share/teplo/venv` (Entware provides `cryptography`), migrates, collects static files, creates or updates the `owner` panel account from `secrets.toml`, switches `/opt/share/teplo/current` and restarts the service. The panel is at `http://192.168.1.1:8088/`.

## Operate

- Service: `/opt/etc/init.d/S90teplo {start|stop|restart|status}`.
- Logs: `/opt/var/log/teplo/teplo.log`; restarts: `/opt/var/log/teplo/restarts.log`.
- The cron watchdog restarts teplo when it stops or exceeds 300 MB RSS and trims the log above 5 MB.
- Secrets and settings are read at start: restart after editing them.

## Enable live control

1. Watch the dry-run journal (`/journal/`) through at least one night and one morning pre-heat.
2. Set `TEPLO_DRY_RUN=false` in `/opt/etc/teplo/teplo.env` and restart.
3. In the admin (`/admin/climate/zone/`), tick `live control` one zone at a time and confirm on the dashboard that the thermostat setpoint follows the target.

## Roll back

```sh
ssh -p 222 root@192.168.1.1 'ls -1t /opt/share/teplo/releases'
ssh -p 222 root@192.168.1.1 'ln -sfn /opt/share/teplo/releases/<previous> /opt/share/teplo/current && /opt/etc/init.d/S90teplo restart'
```
````

Append to `CLAUDE.md` under `## Workflow`:

```markdown
- Deploying to the router or touching `deploy/`: read [`docs/deploy.md`](docs/deploy.md) first.
```

- [ ] **Step 4: Syntax check, commit, push, watch CI**

```bash
for f in deploy/deploy.sh deploy/router/install.sh deploy/router/S90teplo deploy/router/teplo-watchdog.sh; do sh -n "$f" && echo "$f ok"; done
git add deploy docs/deploy.md CLAUDE.md
git commit -m 'Add router deployment scripts and docs'
git push origin stage-1
sh scripts/ci-watch.sh
```

```json:metadata
{"files": ["deploy/router/install.sh", "deploy/router/S90teplo", "deploy/router/teplo-watchdog.sh", "deploy/router/teplo.env.example", "deploy/router/secrets.toml.example", "deploy/deploy.sh", "docs/deploy.md", "CLAUDE.md"], "verifyCommand": "for f in deploy/deploy.sh deploy/router/install.sh deploy/router/S90teplo deploy/router/teplo-watchdog.sh; do sh -n \"$f\" && echo \"$f ok\"; done && git push origin stage-1 && sh scripts/ci-watch.sh", "acceptanceCriteria": ["sh -n passes for every script", "deploy.sh exports requirements without dev, cryptography, cffi, pycparser; installs --no-deps; migrates, collects static, ensures owner, switches current, restarts", "watchdog restarts on missing PID or RSS above 307200 kB, logs restarts, trims log above 5 MB in place", "docs/deploy.md covers install, secrets, deploy, live control, rollback; CLAUDE.md points to it", "CI green"], "modelTier": "standard"}
```

---

### Task 17: Rollout on the router — dry-run first, then live zone by zone

**Goal:** teplo runs on the router in dry-run with all five thermostats online and plausible journaled commands through a night and a morning pre-heat, then controls the zones live one at a time after the owner approves each step.

> **USER-ORDERED GATE — NON-SKIPPABLE.** This task was requested by the user in the current conversation. It MUST NOT be closed by walking around it, by declaring it "verified inline", or by substituting a cheaper check. Close only after every item in `acceptanceCriteria` has been re-validated independently, with output captured.

**Files:**
- Create (gitignored, never committed): `secrets/secrets.toml`
- Router: `/opt/etc/teplo/secrets.toml`, `/opt/etc/teplo/teplo.env`

**Acceptance Criteria:**
- [ ] The owner approved, in chat, running `install.sh` on the router before it ran.
- [ ] `ssh -p 222 root@192.168.1.1 /opt/etc/init.d/S90teplo status` prints `running`.
- [ ] `curl -s -o /dev/null -w '%{http_code}' http://192.168.1.1:8088/login/` prints `200`.
- [ ] The journal holds five `device_availability_changed` entries with `available: true`, one per thermostat slug.
- [ ] Within two minutes of start the journal holds a `zone_command_issued` entry with `dry_run: true` for every zone whose thermostat differs from its target.
- [ ] After the first night the journal shows dry-run commands with `setpoint 28.0` for living-room, kitchen and hallway between 03:30 and 03:35.
- [ ] The owner approved switching to live control in chat; after `TEPLO_DRY_RUN=false` and `live_control` on the first zone, a `zone_command_issued` with `dry_run: false` appears and the dashboard shows that zone's setpoint equal to its target on the next poll.
- [ ] The remaining zones were switched live one at a time, each with the owner's approval and the same check.

**Verify:** `ssh -p 222 root@192.168.1.1 "/opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py shell -c \"from apps.telemetry.models import JournalEntry; print(list(JournalEntry.objects.values_list('kind', 'subject', 'payload')[:20]))\""` with the env file loaded → entries matching the criteria above.

**Steps:**

- [ ] **Step 1: Build the secrets file from the stage-0 spike**

The spike (gitignored `spike/`) holds every thermostat's id and local key in `spike/devices_CaHAbgj.json`; `spike/read_status.py` prints each device's current IP. Run it against the subnet hosts that answer on port 6668 and keep its JSON lines:

```bash
cd spike
"/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe" run --rm --mount 'type=bind,source=C:\Users\filipp\Desktop\home\spike,target=/work' floor-spike python read_status.py devices_CaHAbgj.json $(python3 -c "import socket,concurrent.futures as cf
def p(ip):
    s=socket.socket(); s.settimeout(1.5)
    try: s.connect((ip,6668)); return ip
    except OSError: return None
    finally: s.close()
with cf.ThreadPoolExecutor(64) as ex: print(' '.join(i for i in ex.map(p,[f'192.168.1.{n}' for n in range(2,255)]) if i))") > ../.local/thermostat-ips.jsonl
cd ..
```

Then write `secrets/secrets.toml` with a one-off script (the slug map matches `sites/barsoom`):

```bash
mkdir -p secrets
python3 - <<'EOF'
import json
import secrets as random
from pathlib import Path

SLUGS = {
    'Гостиная': 'thermostat-living-room',
    'Кухня': 'thermostat-kitchen',
    'Теплый пол прихожая': 'thermostat-hallway',
    'Су 1 этаж': 'thermostat-bathroom-1f',
    'С/у 2 этаж': 'thermostat-bathroom-2f',
}
devices = {device['id']: device for device in json.loads(Path('spike/devices_CaHAbgj.json').read_text())}
ips = {row['id']: row for row in map(json.loads, Path('.local/thermostat-ips.jsonl').read_text().splitlines())}
lines = []
for device_id, row in ips.items():
    lines += [
        f"[devices.{SLUGS[row['name']]}]",
        f"address = '{row['ip']}'",
        f"device_id = '{device_id}'",
        f"local_key = '{devices[device_id]['local_key']}'",
        f"protocol = '{row['version']}'",
        '',
    ]
password = random.token_urlsafe(12)
lines += ['[panel]', f"password = '{password}'", '']
Path('secrets/secrets.toml').write_text('\n'.join(lines), encoding='utf-8')
print('devices:', len(ips), 'panel password:', password)
EOF
```

Expected: `devices: 5 panel password: <random>`. Tell the owner the panel password.

- [ ] **Step 2: Get approval, install prerequisites on the router**

Ask the owner in chat: "Install python3 and teplo's service files on the router now (opkg install, cron watchdog line, /opt/etc/init.d/S90teplo)?" Continue only after an explicit yes.

```bash
scp -P 222 -r deploy/router root@192.168.1.1:/tmp/teplo-router
ssh -p 222 root@192.168.1.1 sh /tmp/teplo-router/install.sh
```

Expected: ends with `teplo prerequisites installed`.

- [ ] **Step 3: Upload secrets and settings**

```bash
ssh -p 222 root@192.168.1.1 'umask 077; cat > /opt/etc/teplo/secrets.toml' < secrets/secrets.toml
ssh -p 222 root@192.168.1.1 "sed -i \"s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')|\" /opt/etc/teplo/teplo.env && grep -c DJANGO_SECRET_KEY /opt/etc/teplo/teplo.env"
```

Expected: `1`. Keep `TEPLO_DRY_RUN=true`.

- [ ] **Step 4: Deploy and check the dry-run start**

```bash
sh deploy/deploy.sh
ssh -p 222 root@192.168.1.1 /opt/etc/init.d/S90teplo status
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.1:8088/login/
```

Expected: `deployed <sha>`, `running`, `200`. Two minutes later, read the journal with the Verify command and check the availability and dry-run criteria. Log in to the dashboard with `owner` and the panel password and check that all five zones show floor temperatures.

- [ ] **Step 5: Observe one night and morning**

The next morning, read the journal: dry-run `setpoint 28.0` commands for living-room, kitchen and hallway between 03:30 and 03:35, bathroom-2f `setpoint 30.0` at 03:30, and no errors. Report the observations to the owner.

- [ ] **Step 6: Go live, one zone at a time**

Ask the owner: "Switch teplo to live control, starting with the hallway?" Only after an explicit yes:

```bash
ssh -p 222 root@192.168.1.1 "sed -i 's/^TEPLO_DRY_RUN=.*/TEPLO_DRY_RUN=false/' /opt/etc/teplo/teplo.env && /opt/etc/init.d/S90teplo restart"
```

Tick `live control` for `hallway` in `http://192.168.1.1:8088/admin/climate/zone/`. Within two minutes the journal shows `zone_command_issued` with `dry_run: false` for hallway and the dashboard shows hallway's setpoint equal to its target. Repeat for each remaining zone after the owner approves it.

```json:metadata
{"files": [], "verifyCommand": "ssh -p 222 root@192.168.1.1 \"set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py shell -c \\\"from apps.telemetry.models import JournalEntry; print(list(JournalEntry.objects.values_list('kind', 'subject', 'payload')[:20]))\\\"\"", "acceptanceCriteria": ["owner approved install.sh in chat before it ran", "S90teplo status prints running", "GET http://192.168.1.1:8088/login/ returns 200", "journal has five device_availability_changed entries with available true", "within two minutes, zone_command_issued with dry_run true for every zone that differs from its target", "after the first night, dry-run setpoint 28.0 for living-room, kitchen and hallway between 03:30 and 03:35", "owner approved live control; first live zone shows zone_command_issued dry_run false and setpoint equal to target on the dashboard", "remaining zones switched live one at a time with owner approval and the same check"], "modelTier": "standard", "userGate": true, "tags": ["user-gate"], "requiresUserSpecification": false}
```
