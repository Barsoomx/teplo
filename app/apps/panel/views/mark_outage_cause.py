from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.panel.usecases.get_panel_overview import GetPanelOverviewInputDTO, GetPanelOverviewUseCase
from apps.power.domain.errors import OutageNotFoundError
from apps.power.domain.outage import outage_answer
from apps.power.usecases.mark_outage_cause import MarkOutageCauseInputDTO, MarkOutageCauseUseCase


class MarkOutageCauseView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, outage_id: int) -> HttpResponse:
        cause = outage_answer(request.POST.get('cause', ''))
        if cause is None:
            return HttpResponseBadRequest()

        try:
            MarkOutageCauseUseCase.init().execute(
                MarkOutageCauseInputDTO(outage_id=outage_id, cause=cause, now=timezone.now()),
            )
        except OutageNotFoundError as error:
            raise Http404(error.detail) from error

        overview = GetPanelOverviewUseCase.init().execute(GetPanelOverviewInputDTO(now=timezone.now()))
        return render(request, 'panel/_zones.html', {'overview': overview})
