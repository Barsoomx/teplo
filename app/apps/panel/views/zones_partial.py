from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views.generic import TemplateView

from apps.panel.services.zone_overview import ZoneOverviewService


class ZonesPartialView(LoginRequiredMixin, TemplateView):
    template_name = 'panel/_zones.html'

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context['overview'] = ZoneOverviewService.init().overview(timezone.now())
        return context
