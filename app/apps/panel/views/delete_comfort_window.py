from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.usecases.delete_comfort_window import DeleteComfortWindowInputDTO, DeleteComfortWindowUseCase
from apps.panel.usecases.get_schedule_page import GetSchedulePageInputDTO, GetSchedulePageUseCase


class DeleteComfortWindowView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, window_id: int) -> HttpResponse:
        try:
            output = DeleteComfortWindowUseCase.init().execute(
                DeleteComfortWindowInputDTO(window=window_id, now=timezone.now()),
            )
        except ComfortWindowNotFoundError as error:
            raise Http404(error.detail) from error

        schedule = GetSchedulePageUseCase.init().execute(GetSchedulePageInputDTO())
        zone = schedule.zone(output.zone)
        if zone is None:
            raise Http404(output.zone)

        return render(request, 'panel/_schedule_zone.html', {'zone': zone, 'schedule': schedule, 'failure': None})
