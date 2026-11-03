from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.modes import CommandSource
from apps.climate.usecases.set_guests import SetGuestsInputDTO, SetGuestsUseCase
from apps.panel.forms.guests import GuestsForm
from apps.panel.usecases.get_panel_overview import GetPanelOverviewInputDTO, GetPanelOverviewUseCase


class SetGuestsView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        form = GuestsForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest()

        SetGuestsUseCase.init().execute(
            SetGuestsInputDTO(nights=form.cleaned_data['nights'], now=timezone.now(), source=CommandSource.PANEL),
        )
        overview = GetPanelOverviewUseCase.init().execute(GetPanelOverviewInputDTO(now=timezone.now()))
        return render(request, 'panel/_zones.html', {'overview': overview})
