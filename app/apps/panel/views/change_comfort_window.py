import structlog
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, OperationalError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.usecases.change_comfort_window import ChangeComfortWindowInputDTO, ChangeComfortWindowUseCase
from apps.core.domain.errors import SettingsRejectedError
from apps.panel.forms.comfort_window import ComfortWindowForm
from apps.panel.forms.problems import add_not_saved, add_problems
from apps.panel.forms.schedule_failure import ScheduleFailure, form_messages
from apps.panel.usecases.get_schedule_page import GetSchedulePageInputDTO, GetSchedulePageUseCase

logger = structlog.get_logger(__name__)


class ChangeComfortWindowView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, window_id: int) -> HttpResponse:
        form = ComfortWindowForm(request.POST)
        changed_room = None
        if form.is_valid():
            try:
                output = ChangeComfortWindowUseCase.init().execute(
                    ChangeComfortWindowInputDTO(
                        window=window_id,
                        start=form.cleaned_data['start'],
                        end=form.cleaned_data['end'],
                        now=timezone.now(),
                    ),
                )
            except ComfortWindowNotFoundError as error:
                raise Http404(error.detail) from error
            except SettingsRejectedError as error:
                add_problems(form, error.problems)
            except (OperationalError, IntegrityError) as error:
                logger.warning('schedule_not_saved', action='change', window=window_id, error=str(error))
                add_not_saved(form)
            else:
                changed_room = output.room

        schedule = GetSchedulePageUseCase.init().execute(GetSchedulePageInputDTO())
        room = schedule.room_of_window(window_id) if changed_room is None else schedule.room(changed_room)
        if room is None:
            raise Http404(window_id)

        failure = None
        if form.errors:
            failure = ScheduleFailure(
                action='change',
                messages=form_messages(form),
                window=window_id,
                start=str(request.POST.get('start', '')),
                end=str(request.POST.get('end', '')),
            )
        return render(request, 'panel/_schedule_room.html', {'room': room, 'schedule': schedule, 'failure': failure})
