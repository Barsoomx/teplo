import os

from django.core.exceptions import ImproperlyConfigured

from settings.base import *

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY') or ('teplo-development-only' if DEBUG else '')
if not SECRET_KEY:
    raise ImproperlyConfigured('Set DJANGO_SECRET_KEY: teplo refuses to start without it unless DJANGO_DEBUG is on')
