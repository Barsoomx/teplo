from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.usecases.boost_zone import BoostZoneInputDTO, BoostZoneUseCase
from apps.panel.services.zone_overview import ZoneOverviewService


class BoostZoneView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, slug: str) -> HttpResponse:
        try:
            BoostZoneUseCase.init().execute(BoostZoneInputDTO(zone=slug, now=timezone.now()))
        except ZoneNotFoundError as error:
            raise Http404(error.detail) from error

        overview = ZoneOverviewService.init().overview(timezone.now())
        return render(request, 'panel/_zones.html', {'overview': overview})
