from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.core.domain.errors import SettingsRejectedError
from apps.panel.forms.load_settings import LoadSettingsForm
from apps.panel.forms.problems import add_problems
from apps.panel.usecases.get_settings_page import GetSettingsPageInputDTO, GetSettingsPageUseCase
from apps.power.usecases.update_load_settings import UpdateLoadSettingsInputDTO, UpdateLoadSettingsUseCase


class LoadSettingsView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        form = LoadSettingsForm(request.POST, auto_id='load-settings-%s')
        outcome = None
        if form.is_valid():
            data = form.cleaned_data
            try:
                output = UpdateLoadSettingsUseCase.init().execute(
                    UpdateLoadSettingsInputDTO(
                        start_ceiling_amps=data['start_ceiling_amps'],
                        shed_amps=data['shed_amps'],
                        shed_hold_seconds=data['shed_hold_seconds'],
                        restore_amps=data['restore_amps'],
                        restore_hold_seconds=data['restore_hold_seconds'],
                        start_interval_seconds=data['start_interval_seconds'],
                        meter_lost_after_seconds=data['meter_lost_after_seconds'],
                        default_load_amps=data['default_load_amps'],
                        now=timezone.now(),
                    ),
                )
            except SettingsRejectedError as error:
                add_problems(form, error.problems)
            else:
                outcome = 'saved' if output.changes else 'unchanged'

        page = GetSettingsPageUseCase.init().execute(GetSettingsPageInputDTO())
        if outcome is not None:
            form = LoadSettingsForm.for_settings(page.load_settings)
        return render(
            request,
            'panel/_settings_load_management.html',
            {'load_settings': page.load_settings, 'load_settings_form': form, 'outcome': outcome},
        )
