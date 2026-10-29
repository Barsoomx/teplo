from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views.generic import TemplateView

from apps.panel.usecases.get_panel_overview import GetPanelOverviewInputDTO, GetPanelOverviewUseCase


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'panel/dashboard.html'

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context['overview'] = GetPanelOverviewUseCase.init().execute(GetPanelOverviewInputDTO(now=timezone.now()))
        return context
