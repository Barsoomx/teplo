from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.panel.serializers.outage import OutageSerializer
from apps.power.usecases.list_outages import ListOutagesInputDTO, ListOutagesUseCase


class OutagesApiView(APIView):
    def get(self, _request: Request) -> Response:
        output = ListOutagesUseCase.init().execute(ListOutagesInputDTO())
        return Response(OutageSerializer(output.outages, many=True).data)
