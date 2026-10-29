import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.system.services.runtime import run_teplo


class Command(BaseCommand):
    help = 'Run teplo: control loops and the panel in one process'

    def handle(self, *_args: object, **_options: object) -> None:
        asyncio.run(run_teplo(host=settings.TEPLO_PANEL_HOST, port=settings.TEPLO_PANEL_PORT))
