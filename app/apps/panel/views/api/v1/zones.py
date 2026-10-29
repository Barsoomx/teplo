from django.utils import timezone
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.panel.serializers.zone_overview import ZoneOverviewSerializer
from apps.panel.services.zone_overview import ZoneOverviewService


class ZonesApiView(APIView):
    def get(self, _request: Request) -> Response:
        overview = ZoneOverviewService.init().overview(timezone.now())
        return Response(ZoneOverviewSerializer(overview.zones, many=True).data)
