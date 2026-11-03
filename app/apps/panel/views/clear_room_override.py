from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.modes import CommandSource
from apps.climate.usecases.clear_room_override import ClearRoomOverrideInputDTO, ClearRoomOverrideUseCase
from apps.house.domain.errors import RoomNotFoundError
from apps.panel.usecases.get_panel_overview import GetPanelOverviewInputDTO, GetPanelOverviewUseCase


class ClearRoomOverrideView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, slug: str) -> HttpResponse:
        try:
            ClearRoomOverrideUseCase.init().execute(
                ClearRoomOverrideInputDTO(room=slug, now=timezone.now(), source=CommandSource.PANEL),
            )
        except RoomNotFoundError as error:
            raise Http404(error.detail) from error

        overview = GetPanelOverviewUseCase.init().execute(GetPanelOverviewInputDTO(now=timezone.now()))
        return render(request, 'panel/_zones.html', {'overview': overview})
