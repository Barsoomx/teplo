from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.errors import FireplaceNotFoundError
from apps.climate.domain.modes import CommandSource
from apps.climate.usecases.mark_fireplace import MarkFireplaceInputDTO, MarkFireplaceUseCase
from apps.panel.forms.fireplace_mark import FireplaceMarkForm
from apps.panel.usecases.get_panel_overview import GetPanelOverviewInputDTO, GetPanelOverviewUseCase


class MarkFireplaceView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, slug: str) -> HttpResponse:
        form = FireplaceMarkForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest()

        mark = MarkFireplaceInputDTO(
            room=slug,
            lit=form.cleaned_data['lit'],
            now=timezone.now(),
            source=CommandSource.PANEL,
        )
        try:
            MarkFireplaceUseCase.init().execute(mark)
        except FireplaceNotFoundError as error:
            raise Http404(error.detail) from error

        overview = GetPanelOverviewUseCase.init().execute(GetPanelOverviewInputDTO(now=timezone.now()))
        return render(request, 'panel/_zones.html', {'overview': overview})
