import structlog
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, OperationalError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.usecases.copy_comfort_week import CopyComfortWeekInputDTO, CopyComfortWeekUseCase
from apps.core.domain.errors import SettingsRejectedError
from apps.panel.forms.copy_comfort_week import CopyComfortWeekForm
from apps.panel.forms.problems import add_not_saved, add_problems
from apps.panel.forms.schedule_failure import ScheduleFailure, form_messages
from apps.panel.usecases.get_schedule_page import GetSchedulePageInputDTO, GetSchedulePageUseCase

logger = structlog.get_logger(__name__)


class CopyComfortWeekView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, slug: str) -> HttpResponse:
        form = CopyComfortWeekForm(request.POST)
        if form.is_valid():
            try:
                CopyComfortWeekUseCase.init().execute(
                    CopyComfortWeekInputDTO(zone=slug, to_zones=tuple(form.cleaned_data['zones']), now=timezone.now()),
                )
            except ZoneNotFoundError as error:
                raise Http404(error.detail) from error
            except SettingsRejectedError as error:
                add_problems(form, error.problems)
            except (OperationalError, IntegrityError) as error:
                logger.warning('schedule_not_saved', action='copy_week', zone=slug, error=str(error))
                add_not_saved(form)

        schedule = GetSchedulePageUseCase.init().execute(GetSchedulePageInputDTO())
        failure = None
        if form.errors:
            failure = ScheduleFailure(
                action='copy_week',
                messages=form_messages(form),
                zone=slug,
                chosen=tuple(request.POST.getlist('zones')),
            )
        return render(request, 'panel/_schedule_zones.html', {'schedule': schedule, 'failure': failure})
