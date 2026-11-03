from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path, register_converter

from apps.panel.converters import WeekdayConverter
from apps.panel.views.add_comfort_window import AddComfortWindowView
from apps.panel.views.api.v1.loads import LoadsApiView
from apps.panel.views.api.v1.outages import OutagesApiView
from apps.panel.views.api.v1.phases import PhasesApiView
from apps.panel.views.api.v1.plugs import PlugsApiView
from apps.panel.views.api.v1.rooms import RoomsApiView
from apps.panel.views.api.v1.sensors import SensorsApiView
from apps.panel.views.api.v1.zones import ZonesApiView
from apps.panel.views.boost_zone import BoostZoneView
from apps.panel.views.change_comfort_window import ChangeComfortWindowView
from apps.panel.views.clear_room_override import ClearRoomOverrideView
from apps.panel.views.climate_settings import ClimateSettingsView
from apps.panel.views.copy_comfort_day import CopyComfortDayView
from apps.panel.views.copy_comfort_week import CopyComfortWeekView
from apps.panel.views.dashboard import DashboardView
from apps.panel.views.delete_comfort_window import DeleteComfortWindowView
from apps.panel.views.journal import JournalView
from apps.panel.views.journal_outage_cause import JournalOutageCauseView
from apps.panel.views.load_settings import LoadSettingsView
from apps.panel.views.loads_settings import LoadsSettingsView
from apps.panel.views.mark_outage_cause import MarkOutageCauseView
from apps.panel.views.override_room import OverrideRoomView
from apps.panel.views.phases_partial import PhasesPartialView
from apps.panel.views.room_settings import RoomSettingsView
from apps.panel.views.schedule_page import SchedulePageView
from apps.panel.views.set_guests import SetGuestsView
from apps.panel.views.settings_page import SettingsPageView
from apps.panel.views.zone_settings import ZoneSettingsView
from apps.panel.views.zones_partial import ZonesPartialView

app_name = 'panel'

register_converter(WeekdayConverter, 'weekday')

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('zones/', ZonesPartialView.as_view(), name='zones'),
    path('zones/<slug:slug>/boost/', BoostZoneView.as_view(), name='boost-zone'),
    path('guests/', SetGuestsView.as_view(), name='set-guests'),
    path('rooms/<slug:slug>/override/', OverrideRoomView.as_view(), name='override-room'),
    path('rooms/<slug:slug>/override/clear/', ClearRoomOverrideView.as_view(), name='clear-room-override'),
    path('phases/', PhasesPartialView.as_view(), name='phases'),
    path('outages/<int:outage_id>/cause/', MarkOutageCauseView.as_view(), name='mark-outage-cause'),
    path('schedule/', SchedulePageView.as_view(), name='schedule'),
    path('schedule/windows/<int:window_id>/', ChangeComfortWindowView.as_view(), name='change-comfort-window'),
    path('schedule/windows/<int:window_id>/delete/', DeleteComfortWindowView.as_view(), name='delete-comfort-window'),
    path(
        'schedule/<slug:slug>/days/<weekday:weekday>/windows/',
        AddComfortWindowView.as_view(),
        name='add-comfort-window',
    ),
    path('schedule/<slug:slug>/days/<weekday:weekday>/copy/', CopyComfortDayView.as_view(), name='copy-comfort-day'),
    path('schedule/<slug:slug>/copy/', CopyComfortWeekView.as_view(), name='copy-comfort-week'),
    path('settings/', SettingsPageView.as_view(), name='settings'),
    path('settings/rooms/<slug:slug>/', RoomSettingsView.as_view(), name='room-settings'),
    path('settings/zones/<slug:slug>/', ZoneSettingsView.as_view(), name='zone-settings'),
    path('settings/climate/', ClimateSettingsView.as_view(), name='climate-settings'),
    path('settings/load-management/', LoadSettingsView.as_view(), name='load-settings'),
    path('settings/loads/', LoadsSettingsView.as_view(), name='loads-settings'),
    path('journal/', JournalView.as_view(), name='journal'),
    path('journal/outages/<int:outage_id>/cause/', JournalOutageCauseView.as_view(), name='journal-outage-cause'),
    path('login/', LoginView.as_view(template_name='panel/login.html'), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('api/v1/zones/', ZonesApiView.as_view(), name='api-v1-zones'),
    path('api/v1/rooms/', RoomsApiView.as_view(), name='api-v1-rooms'),
    path('api/v1/plugs/', PlugsApiView.as_view(), name='api-v1-plugs'),
    path('api/v1/loads/', LoadsApiView.as_view(), name='api-v1-loads'),
    path('api/v1/sensors/', SensorsApiView.as_view(), name='api-v1-sensors'),
    path('api/v1/phases/', PhasesApiView.as_view(), name='api-v1-phases'),
    path('api/v1/outages/', OutagesApiView.as_view(), name='api-v1-outages'),
]
