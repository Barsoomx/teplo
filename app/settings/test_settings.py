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
