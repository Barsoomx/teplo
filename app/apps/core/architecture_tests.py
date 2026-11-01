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


def test_only_the_shared_builder_imports_tinytuya() -> None:
    importers = [
        path.relative_to(APPS_ROOT).as_posix()
        for path in _app_modules()
        if any(module.split('.')[0] == 'tinytuya' for module in _imports(path))
    ]

    assert importers == [TUYA_CLIENT_BUILDER]
