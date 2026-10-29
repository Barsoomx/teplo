# teplo

Local-first heating and load controller that runs on a home router.
Before changing control logic, device drivers, persistence or failure handling, read [`docs/design.md`](docs/design.md); update it in the same change when behaviour changes.

## Invariants

- **Offline runtime.** Devices are reached over the LAN; the internet only carries Telegram alerts and NTP.
- **Router target.** Production is one Python 3.13 process on an Entware router (aarch64, ~600 MB free RAM, USB flash). Dependencies are pure Python or provided by Entware (`python3-cryptography`). Writes to flash are batched.
- **Public repository.** Secrets (device addresses, device ids, local keys, tokens, passwords) live only in the router's secrets file; code and migrations reference devices by slug.
- **Configuration is data.** Schedules, tariffs, thresholds and priorities come from the database, seeded by `sites/<name>` data migrations and edited in the panel.
- **Dry-run first.** A new control path ships behind dry-run and journals the commands it would send.
- **Device safety.** Monitor-only loads expose no switching operation. A thermostat gets at most one state change per zone per 30 minutes.

## Architecture

Lightweight DDD in the altyn-backend style: Django models are the persistent entities, use cases orchestrate, pure domain policies decide.

```
app/
  settings/              settings.py (os.getenv), test_settings.py, urls.py, asgi.py
  apps/
    core/                shared kernel: BaseUseCase, DomainError, DomainEvent, base command, runtime
    <context>/
      domain/            pure policies, value objects, ports (Protocol), errors
      usecases/          <verb>_<noun>.py: <Verb><Noun>UseCase, <Verb><Noun>InputDTO, <Verb><Noun>OutputDTO
      models/            one Django model per file
      repositories/      concrete ORM repositories
      services/          stateful helpers: state store, registries
      drivers/           device protocol drivers (devices context)
      views/             panel views and api/v1 views, one view per file
      serializers/       api/v1 serializers
      management/commands/
      migrations/
      factories.py
  sites/<name>/          site app: data migrations describing one house
esphome/                 ESPHome device configurations
deploy/                  router install and deploy scripts
```

Contexts: `core`, `devices`, `climate`, `power`, `tariffs`, `telemetry`, `notifications`, `system`, `panel`.

- **Use cases** extend `BaseUseCase[TInput, TOutput]`: the public `execute(input_dto)` handles logging, `DomainError` and event dispatch, the subclass implements `_execute`; `UseCaseTransactional` runs `_execute` in a transaction. Views, management commands and runtime loops only call use cases.
- **Use cases are synchronous**; the asyncio runtime calls them through `sync_to_async`.
- **Domain policies** (`climate/domain`, `power/domain`) are pure: value objects in, decisions out, no imports from Django, drivers or the network. AST architecture tests enforce the boundaries.
- **Devices** are reached through capability ports declared as `Protocol` in `devices/domain/capabilities.py` (`FloorThermostat`, `SwitchablePlug`, `TemperatureSensor`, `PhaseMeter`, `ClimateUnit`, `EvCharger`). A new device type is a driver implementing a port; an unsupported one is a stub driver raising `DriverNotImplementedError`.
- **Domain events** (`DomainEvent`) are collected by the use case and dispatched in-process after success to `@domain_event_handler` handlers: journal, Telegram.
- **Errors** subclass `DomainError(detail, error_code=...)`.
- **DTOs and value objects** are frozen dataclasses and `StrEnum`s, which keeps the router install free of compiled wheels.

Use case shape:

```python
class ShedPhaseLoadsUseCase(BaseUseCase[ShedPhaseLoadsInputDTO, ShedPhaseLoadsOutputDTO]):
    def __init__(self, loads: LoadRepository, switcher: LoadSwitcher) -> None:
        super().__init__()
        self._loads = loads
        self._switcher = switcher

    @classmethod
    def init(cls) -> 'ShedPhaseLoadsUseCase':
        return cls(loads=LoadRepository(), switcher=LoadSwitcher.init())

    def _execute(self, input_dto: ShedPhaseLoadsInputDTO) -> ShedPhaseLoadsOutputDTO:
        ...
```

Where teplo deliberately differs from altyn-backend: tests sit next to the module, constructors only assign and defaults come from `init()`, one view per file, unit tests use stubs instead of patching.

## Code style

- Single quotes, absolute imports, imports at module top (a local import only to break a cycle).
- Built-in generics (`list[int]`, `dict[str, Zone]`); every signature is annotated.
- Code explains itself; write a comment or docstring only for what the code cannot say.
- Private attributes and methods by default (`_name`); expose reads through properties.
- Constructors only assign; construction logic lives in an `init` classmethod.
- Catch specific exceptions; let the rest propagate.
- A blank line follows `return` or `raise` when more code follows in the block.
- Long calls and literals put one argument per line with a trailing comma; logging is structlog with snake_case event names:

```python
logger.info(
    'zone_shed',
    zone=zone.slug,
    phase=phase.name,
    current=str(current),
)
```

- SOLID: small classes with one reason to change, dependencies injected through constructors.

## Tests

- pytest functions named `test_<what>_<scenario>` in `<module>_tests.py` next to the tested module, laid out Arrange / Act / Assert.
- All preparation lives in typed fixtures. A fixture passed into a test is named `f_<name>`, or `m_<name>` when it is a mock; objects built inside a test have plain names. Shared fixtures live in `conftest.py` at `app/` and per app.
- Stubs over mocks: implement the port in a stub and pass it through the constructor. View tests use mocks only, patched by import path string (`mock.patch('apps.panel.views...')`; `app/` is the import root); `monkeypatch.setattr` and `patch.object` stay unused.
- ORM tests declare `pytestmark = pytest.mark.django_db`; time is frozen with freezegun; API tests compare the whole `response.json()`.
- Django models come from factory_boy factories that declare only the fields tests use:

```python
class ZoneFactory(factory.django.DjangoModelFactory):
    slug = factory.Sequence(lambda n: f'zone_{n}')
    name = factory.Sequence(lambda n: f'Zone {n}')

    class Meta:
        model = ZoneModel
```

## Running code

- Application code runs in Docker: `docker compose run --rm app <command>`.
- Tests run in CI (GitHub Actions) on every push. Locally pytest in Docker freezes the owner's WSL, so run tests locally only when the owner asks, narrowly: `docker compose run --rm app pytest -p no:xdist <path>`.

## Workflow

- Talk to the owner in Russian; code, docs and commit messages are in English.
- Commit and push only when the owner asks.
