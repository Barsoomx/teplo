from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from apps.telemetry.repositories.journal import JournalRepository

JOURNAL_LIMIT = 100


class JournalView(LoginRequiredMixin, TemplateView):
    template_name = 'panel/journal.html'

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context['entries'] = JournalRepository.latest(limit=JOURNAL_LIMIT)
        return context
