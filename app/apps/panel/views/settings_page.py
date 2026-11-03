from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from apps.panel.forms.climate_settings import ClimateSettingsForm
from apps.panel.forms.load_settings import LoadSettingsForm
from apps.panel.forms.loads import load_formset, load_rows
from apps.panel.forms.zone_settings import ZoneSettingsForm
from apps.panel.usecases.get_settings_page import GetSettingsPageInputDTO, GetSettingsPageUseCase


class SettingsPageView(LoginRequiredMixin, TemplateView):
    template_name = 'panel/settings.html'

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        page = GetSettingsPageUseCase.init().execute(GetSettingsPageInputDTO())
        formset = load_formset(page.loads)
        context['page'] = page
        context['zone_forms'] = [(zone, ZoneSettingsForm.for_zone(zone)) for zone in page.zones]
        context['climate_form'] = ClimateSettingsForm.for_settings(page.climate)
        context['load_settings_form'] = LoadSettingsForm.for_settings(page.load_settings)
        context['formset'] = formset
        context['rows'] = load_rows(formset, page.loads)
        return context
