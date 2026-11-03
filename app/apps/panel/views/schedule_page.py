from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from apps.panel.usecases.get_schedule_page import GetSchedulePageInputDTO, GetSchedulePageUseCase


class SchedulePageView(LoginRequiredMixin, TemplateView):
    template_name = 'panel/schedule.html'

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context['schedule'] = GetSchedulePageUseCase.init().execute(GetSchedulePageInputDTO())
        return context
