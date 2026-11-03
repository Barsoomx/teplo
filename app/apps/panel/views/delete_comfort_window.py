import structlog
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, OperationalError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.usecases.delete_comfort_window import DeleteComfortWindowInputDTO, DeleteComfortWindowUseCase
from apps.panel.forms.problems import NOT_SAVED
from apps.panel.forms.schedule_failure import ScheduleFailure
from apps.panel.usecases.get_schedule_page import GetSchedulePageInputDTO, GetSchedulePageUseCase

logger = structlog.get_logger(__name__)


class DeleteComfortWindowView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, window_id: int) -> HttpResponse:
        deleted_from = None
        failure = None
        try:
            output = DeleteComfortWindowUseCase.init().execute(
                DeleteComfortWindowInputDTO(window=window_id, now=timezone.now()),
            )
        except ComfortWindowNotFoundError as error:
            raise Http404(error.detail) from error
        except (OperationalError, IntegrityError) as error:
            logger.warning('schedule_not_saved', action='delete', window=window_id, error=str(error))
            failure = ScheduleFailure(action='delete', messages=(str(NOT_SAVED),), window=window_id)
        else:
            deleted_from = output.zone

        schedule = GetSchedulePageUseCase.init().execute(GetSchedulePageInputDTO())
        zone = schedule.zone_of_window(window_id) if deleted_from is None else schedule.zone(deleted_from)
        if zone is None:
            raise Http404(window_id)

        return render(request, 'panel/_schedule_zone.html', {'zone': zone, 'schedule': schedule, 'failure': failure})
