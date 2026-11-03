from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from apps.panel.views.api.v1.loads import LoadsApiView
from apps.panel.views.api.v1.outages import OutagesApiView
from apps.panel.views.api.v1.phases import PhasesApiView
from apps.panel.views.api.v1.plugs import PlugsApiView
from apps.panel.views.api.v1.sensors import SensorsApiView
from apps.panel.views.api.v1.zones import ZonesApiView
from apps.panel.views.boost_zone import BoostZoneView
from apps.panel.views.dashboard import DashboardView
from apps.panel.views.journal import JournalView
from apps.panel.views.journal_outage_cause import JournalOutageCauseView
from apps.panel.views.mark_outage_cause import MarkOutageCauseView
from apps.panel.views.phases_partial import PhasesPartialView
from apps.panel.views.zones_partial import ZonesPartialView

app_name = 'panel'

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('zones/', ZonesPartialView.as_view(), name='zones'),
    path('zones/<slug:slug>/boost/', BoostZoneView.as_view(), name='boost-zone'),
    path('phases/', PhasesPartialView.as_view(), name='phases'),
    path('outages/<int:outage_id>/cause/', MarkOutageCauseView.as_view(), name='mark-outage-cause'),
    path('journal/', JournalView.as_view(), name='journal'),
    path('journal/outages/<int:outage_id>/cause/', JournalOutageCauseView.as_view(), name='journal-outage-cause'),
    path('login/', LoginView.as_view(template_name='panel/login.html'), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('api/v1/zones/', ZonesApiView.as_view(), name='api-v1-zones'),
    path('api/v1/plugs/', PlugsApiView.as_view(), name='api-v1-plugs'),
    path('api/v1/loads/', LoadsApiView.as_view(), name='api-v1-loads'),
    path('api/v1/sensors/', SensorsApiView.as_view(), name='api-v1-sensors'),
    path('api/v1/phases/', PhasesApiView.as_view(), name='api-v1-phases'),
    path('api/v1/outages/', OutagesApiView.as_view(), name='api-v1-outages'),
]
