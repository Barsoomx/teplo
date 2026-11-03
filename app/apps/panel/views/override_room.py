from contextlib import suppress

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.errors import RoomNotAdjustableError
from apps.climate.domain.modes import CommandSource
from apps.climate.usecases.override_room import OverrideRoomInputDTO, OverrideRoomUseCase
from apps.core.domain.errors import SettingsRejectedError
from apps.house.domain.errors import RoomNotFoundError
from apps.panel.forms.override_step import OverrideStepForm
from apps.panel.usecases.get_panel_overview import GetPanelOverviewInputDTO, GetPanelOverviewUseCase
from apps.system.services.clock_trust import clock_trust_source, read_clock


class OverrideRoomView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, slug: str) -> HttpResponse:
        form = OverrideStepForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest()

        reading = read_clock(clock_trust_source())
        override = OverrideRoomInputDTO(
            room=slug,
            change=form.cleaned_data['step'],
            value=None,
            now=reading.now,
            clock_trusted=reading.trusted,
            source=CommandSource.PANEL,
        )
        try:
            with suppress(SettingsRejectedError):
                OverrideRoomUseCase.init().execute(override)
        except (RoomNotFoundError, RoomNotAdjustableError) as error:
            raise Http404(error.detail) from error

        overview = GetPanelOverviewUseCase.init().execute(GetPanelOverviewInputDTO(now=timezone.now()))
        return render(request, 'panel/_zones.html', {'overview': overview})
