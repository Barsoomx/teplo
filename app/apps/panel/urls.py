from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from apps.panel.views.api.v1.plugs import PlugsApiView
from apps.panel.views.api.v1.zones import ZonesApiView
from apps.panel.views.boost_zone import BoostZoneView
from apps.panel.views.dashboard import DashboardView
from apps.panel.views.journal import JournalView
from apps.panel.views.zones_partial import ZonesPartialView

app_name = 'panel'

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('zones/', ZonesPartialView.as_view(), name='zones'),
    path('zones/<slug:slug>/boost/', BoostZoneView.as_view(), name='boost-zone'),
    path('journal/', JournalView.as_view(), name='journal'),
    path('login/', LoginView.as_view(template_name='panel/login.html'), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('api/v1/zones/', ZonesApiView.as_view(), name='api-v1-zones'),
    path('api/v1/plugs/', PlugsApiView.as_view(), name='api-v1-plugs'),
]
