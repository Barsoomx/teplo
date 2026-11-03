import structlog
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, OperationalError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.usecases.update_room_climate import UpdateRoomClimateInputDTO, UpdateRoomClimateUseCase
from apps.core.domain.errors import SettingsRejectedError
from apps.house.domain.errors import RoomNotFoundError
from apps.panel.forms.problems import add_not_saved, add_problems
from apps.panel.forms.room_settings import RoomSettingsForm
from apps.panel.usecases.get_settings_page import GetSettingsPageInputDTO, GetSettingsPageUseCase

logger = structlog.get_logger(__name__)


class RoomSettingsView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, slug: str) -> HttpResponse:
        page = GetSettingsPageUseCase.init().execute(GetSettingsPageInputDTO())
        room = page.room(slug)
        if room is None:
            raise Http404(slug)

        form = RoomSettingsForm.for_room(room, request.POST)
        outcome = None
        if form.is_valid():
            data = form.cleaned_data
            try:
                output = UpdateRoomClimateUseCase.init().execute(
                    UpdateRoomClimateInputDTO(
                        room=slug,
                        comfort_target=data['comfort_target'],
                        setback_target=data['setback_target'],
                        backup_below=data.get('backup_below'),
                        backup_hysteresis=data.get('backup_hysteresis'),
                        now=timezone.now(),
                    ),
                )
            except RoomNotFoundError as error:
                raise Http404(error.detail) from error
            except SettingsRejectedError as error:
                add_problems(form, error.problems)
            except (OperationalError, IntegrityError) as error:
                logger.warning('room_settings_not_saved', room=slug, error=str(error))
                add_not_saved(form)
            else:
                outcome = 'saved' if output.changes else 'unchanged'

        if outcome is not None:
            page = GetSettingsPageUseCase.init().execute(GetSettingsPageInputDTO())
            room = page.room(slug)
            if room is None:
                raise Http404(slug)

            form = RoomSettingsForm.for_room(room)
        return render(
            request,
            'panel/_settings_room.html',
            {'room': room, 'room_form': form, 'outcome': outcome},
        )
