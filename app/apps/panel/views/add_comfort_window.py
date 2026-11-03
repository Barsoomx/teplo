import structlog
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, OperationalError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.usecases.add_comfort_window import AddComfortWindowInputDTO, AddComfortWindowUseCase
from apps.core.domain.errors import SettingsRejectedError
from apps.panel.forms.comfort_window import ComfortWindowForm
from apps.panel.forms.problems import add_not_saved, add_problems
from apps.panel.forms.schedule_failure import ScheduleFailure, form_messages
from apps.panel.usecases.get_schedule_page import GetSchedulePageInputDTO, GetSchedulePageUseCase

logger = structlog.get_logger(__name__)


class AddComfortWindowView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, slug: str, weekday: int) -> HttpResponse:
        form = ComfortWindowForm(request.POST)
        if form.is_valid():
            try:
                AddComfortWindowUseCase.init().execute(
                    AddComfortWindowInputDTO(
                        zone=slug,
                        weekday=weekday,
                        start=form.cleaned_data['start'],
                        end=form.cleaned_data['end'],
                        now=timezone.now(),
                    ),
                )
            except ZoneNotFoundError as error:
                raise Http404(error.detail) from error
            except SettingsRejectedError as error:
                add_problems(form, error.problems)
            except (OperationalError, IntegrityError) as error:
                logger.warning('schedule_not_saved', action='add', zone=slug, weekday=weekday, error=str(error))
                add_not_saved(form)

        schedule = GetSchedulePageUseCase.init().execute(GetSchedulePageInputDTO())
        zone = schedule.zone(slug)
        if zone is None:
            raise Http404(slug)

        failure = None
        if form.errors:
            failure = ScheduleFailure(
                action='add',
                messages=form_messages(form),
                weekday=weekday,
                start=str(request.POST.get('start', '')),
                end=str(request.POST.get('end', '')),
            )
        return render(request, 'panel/_schedule_zone.html', {'zone': zone, 'schedule': schedule, 'failure': failure})
