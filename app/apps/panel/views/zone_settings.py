import structlog
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, OperationalError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.usecases.update_zone_settings import UpdateZoneSettingsInputDTO, UpdateZoneSettingsUseCase
from apps.core.domain.errors import SettingsRejectedError
from apps.panel.forms.problems import add_not_saved, add_problems
from apps.panel.forms.zone_settings import ZoneSettingsForm
from apps.panel.usecases.get_settings_page import GetSettingsPageInputDTO, GetSettingsPageUseCase

logger = structlog.get_logger(__name__)


class ZoneSettingsView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, slug: str) -> HttpResponse:
        page = GetSettingsPageUseCase.init().execute(GetSettingsPageInputDTO())
        zone = page.zone(slug)
        if zone is None:
            raise Http404(slug)

        form = ZoneSettingsForm.for_zone(zone, request.POST)
        outcome = None
        if form.is_valid():
            data = form.cleaned_data
            try:
                output = UpdateZoneSettingsUseCase.init().execute(
                    UpdateZoneSettingsInputDTO(
                        zone=slug,
                        name=data['name'],
                        heating_enabled=data['heating_enabled'],
                        comfort_floor_setpoint=data['comfort_floor_setpoint'],
                        night_floor_setpoint=data['night_floor_setpoint'],
                        boost_floor_setpoint=data['boost_floor_setpoint'],
                        idle_floor_setpoint=data['idle_floor_setpoint'],
                        floor_limit_setpoint=data['floor_limit_setpoint'],
                        comfort_air_target=data.get('comfort_air_target'),
                        night_air_target=data.get('night_air_target'),
                        preheat_lead_minutes=data['preheat_lead_minutes'],
                        now=timezone.now(),
                    ),
                )
            except ZoneNotFoundError as error:
                raise Http404(error.detail) from error
            except SettingsRejectedError as error:
                add_problems(form, error.problems)
            except (OperationalError, IntegrityError) as error:
                logger.warning('zone_settings_not_saved', zone=slug, error=str(error))
                add_not_saved(form)
            else:
                outcome = 'saved' if output.changes else 'unchanged'

        if outcome is not None:
            page = GetSettingsPageUseCase.init().execute(GetSettingsPageInputDTO())
            zone = page.zone(slug)
            if zone is None:
                raise Http404(slug)

            form = ZoneSettingsForm.for_zone(zone)
        return render(
            request,
            'panel/_settings_zone.html',
            {'zone': zone, 'form': form, 'outcome': outcome},
        )
