from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from apps.panel.usecases.get_journal_timeline import GetJournalTimelineInputDTO, GetJournalTimelineUseCase

JOURNAL_LIMIT = 100


class JournalView(LoginRequiredMixin, TemplateView):
    template_name = 'panel/journal.html'

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        output = GetJournalTimelineUseCase.init().execute(GetJournalTimelineInputDTO(limit=JOURNAL_LIMIT))
        context['days'] = output.days
        return context
