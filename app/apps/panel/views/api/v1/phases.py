from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.panel.serializers.phase_current import PhaseCurrentSerializer
from apps.panel.usecases.get_phase_currents import GetPhaseCurrentsInputDTO, GetPhaseCurrentsUseCase


class PhasesApiView(APIView):
    def get(self, _request: Request) -> Response:
        output = GetPhaseCurrentsUseCase.init().execute(GetPhaseCurrentsInputDTO())
        phases = output.meter.phases if output.meter else ()
        return Response(PhaseCurrentSerializer(phases, many=True).data)
