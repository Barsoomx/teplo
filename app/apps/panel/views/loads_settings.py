from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.core.domain.errors import SettingsRejectedError
from apps.panel.forms.loads import add_load_problems, load_formset, load_rows
from apps.panel.usecases.get_settings_page import GetSettingsPageInputDTO, GetSettingsPageUseCase
from apps.power.domain.errors import LoadNotFoundError
from apps.power.domain.load_config import LoadConfig
from apps.power.usecases.update_loads import LoadEdit, UpdateLoadsInputDTO, UpdateLoadsUseCase


class LoadsSettingsView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        formset = load_formset((), request.POST)
        outcome = None
        if formset.is_valid():
            try:
                output = UpdateLoadsUseCase.init().execute(
                    UpdateLoadsInputDTO(
                        loads=tuple(
                            LoadEdit(
                                slug=form.cleaned_data['slug'],
                                config=LoadConfig(
                                    priority=form.cleaned_data['priority'],
                                    phase=form.cleaned_data['phase'],
                                    nominal_amps=form.cleaned_data['nominal_amps'],
                                ),
                            )
                            for form in formset.forms
                            if form.cleaned_data
                        ),
                        now=timezone.now(),
                    ),
                )
            except LoadNotFoundError as error:
                raise Http404(error.detail) from error
            except SettingsRejectedError as error:
                add_load_problems(formset, error.problems)
            else:
                outcome = 'saved' if output.changes else 'unchanged'

        page = GetSettingsPageUseCase.init().execute(GetSettingsPageInputDTO())
        if outcome is not None:
            formset = load_formset(page.loads)
        return render(
            request,
            'panel/_settings_loads.html',
            {'formset': formset, 'rows': load_rows(formset, page.loads), 'outcome': outcome},
        )
