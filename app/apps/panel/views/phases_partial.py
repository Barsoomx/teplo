from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from apps.panel.usecases.get_phase_currents import GetPhaseCurrentsInputDTO, GetPhaseCurrentsUseCase


class PhasesPartialView(LoginRequiredMixin, TemplateView):
    template_name = 'panel/_phases.html'

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context['phases'] = GetPhaseCurrentsUseCase.init().execute(GetPhaseCurrentsInputDTO())
        return context
