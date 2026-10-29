from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from apps.telemetry.usecases.list_journal_entries import ListJournalEntriesInputDTO, ListJournalEntriesUseCase

JOURNAL_LIMIT = 100


class JournalView(LoginRequiredMixin, TemplateView):
    template_name = 'panel/journal.html'

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        output = ListJournalEntriesUseCase.init().execute(ListJournalEntriesInputDTO(limit=JOURNAL_LIMIT))
        context['entries'] = output.entries
        return context
