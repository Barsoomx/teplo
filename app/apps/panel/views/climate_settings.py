import structlog
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, OperationalError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.usecases.update_climate_settings import UpdateClimateSettingsInputDTO, UpdateClimateSettingsUseCase
from apps.core.domain.errors import SettingsRejectedError
from apps.panel.forms.climate_settings import ClimateSettingsForm
from apps.panel.forms.problems import add_not_saved, add_problems
from apps.panel.usecases.get_settings_page import GetSettingsPageInputDTO, GetSettingsPageUseCase

logger = structlog.get_logger(__name__)


class ClimateSettingsView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        form = ClimateSettingsForm(request.POST, auto_id='climate-%s')
        outcome = None
        if form.is_valid():
            data = form.cleaned_data
            try:
                output = UpdateClimateSettingsUseCase.init().execute(
                    UpdateClimateSettingsInputDTO(
                        air_start_offset=data['air_start_offset'],
                        initial_early_stop_offset=data['initial_early_stop_offset'],
                        air_min_band=data['air_min_band'],
                        learned_mornings_needed=data['learned_mornings_needed'],
                        preheat_lead_cap_minutes=data['preheat_lead_cap_minutes'],
                        guests_end_hour=data['guests_end_hour'],
                        now=timezone.now(),
                    ),
                )
            except SettingsRejectedError as error:
                add_problems(form, error.problems)
            except (OperationalError, IntegrityError) as error:
                logger.warning('climate_settings_not_saved', error=str(error))
                add_not_saved(form)
            else:
                outcome = 'saved' if output.changes else 'unchanged'

        page = GetSettingsPageUseCase.init().execute(GetSettingsPageInputDTO())
        if outcome is not None:
            form = ClimateSettingsForm.for_settings(page.climate)
        return render(
            request,
            'panel/_settings_climate.html',
            {'climate': page.climate, 'climate_form': form, 'outcome': outcome},
        )
