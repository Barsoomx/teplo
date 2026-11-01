import ast
from pathlib import Path

import pytest

APPS_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_PACKAGES = ('django', 'rest_framework', 'tinytuya', 'paho', 'requests', 'uvicorn', 'whitenoise')
FORBIDDEN_MODULES = ('http', 'socket', 'ssl', 'urllib.request', 'urllib.error')
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
TUYA_CLIENT_BUILDER = 'devices/drivers/tuya_client.py'


def _domain_modules() -> list[Path]:
    return sorted(path for path in APPS_ROOT.glob('*/domain/**/*.py') if not path.name.endswith('_tests.py'))


def _app_modules() -> list[Path]:
    return sorted(path for path in APPS_ROOT.glob('**/*.py') if not path.name.endswith('_tests.py'))


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
            modules.extend(f'{node.module}.{alias.name}' for alias in node.names)
    return modules


def _is_forbidden(module: str) -> bool:
    parts = module.split('.')
    if parts[0] in FORBIDDEN_PACKAGES:
        return True

    if any(module == name or module.startswith(f'{name}.') for name in FORBIDDEN_MODULES):
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
        ('urllib.request', True),
        ('urllib.error', True),
        ('http.client', True),
        ('socket', True),
        ('ssl', True),
        ('urllib.parse', False),
        ('httpx', False),
    ],
)
def test_forbidden_import_rules(module: str, expected: bool) -> None:
    assert _is_forbidden(module) is expected


@pytest.fixture
def f_network_module(tmp_path: Path) -> Path:
    path = tmp_path / 'policy.py'
    path.write_text('from urllib import parse, request\nfrom http import client\n', encoding='utf-8')
    return path


def test_from_import_of_a_network_module_is_caught(f_network_module: Path) -> None:
    assert [module for module in _imports(f_network_module) if _is_forbidden(module)] == [
        'urllib.request',
        'http',
        'http.client',
    ]


def test_only_the_shared_builder_imports_tinytuya() -> None:
    importers = [
        path.relative_to(APPS_ROOT).as_posix()
        for path in _app_modules()
        if any(module.split('.')[0] == 'tinytuya' for module in _imports(path))
    ]

    assert importers == [TUYA_CLIENT_BUILDER]
