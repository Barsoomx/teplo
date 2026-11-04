from contextlib import suppress

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.errors import ClockUntrustedError
from apps.climate.domain.modes import CommandSource
from apps.climate.usecases.set_guests import SetGuestsInputDTO, SetGuestsUseCase
from apps.panel.forms.guests import GuestsForm
from apps.panel.usecases.get_panel_overview import GetPanelOverviewInputDTO, GetPanelOverviewUseCase
from apps.system.services.clock_trust import clock_trust_source, read_clock


class SetGuestsView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        form = GuestsForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest()

        reading = read_clock(clock_trust_source())
        guests = SetGuestsInputDTO(
            nights=form.cleaned_data['nights'],
            now=reading.now,
            clock_trusted=reading.trusted,
            source=CommandSource.PANEL,
        )
        with suppress(ClockUntrustedError):
            SetGuestsUseCase.init().execute(guests)
        overview = GetPanelOverviewUseCase.init().execute(GetPanelOverviewInputDTO(now=timezone.now()))
        return render(request, 'panel/_zones.html', {'overview': overview})
