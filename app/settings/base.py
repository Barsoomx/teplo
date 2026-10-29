import os
from pathlib import Path

import structlog

from settings.utils import to_bool, to_list

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_DIR = BASE_DIR.parent / '.local'

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

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'plain': {'format': '{asctime} {levelname} {name}: {message}', 'style': '{'},
    },
    'handlers': {
        'stdout': {'class': 'logging.StreamHandler', 'stream': 'ext://sys.stdout', 'formatter': 'plain'},
    },
    'loggers': {
        'django': {'handlers': ['stdout'], 'level': 'WARNING'},
    },
}

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt='iso'),
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer(ensure_ascii=False),
    ],
)
