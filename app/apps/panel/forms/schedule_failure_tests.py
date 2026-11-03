from django.http import QueryDict

from apps.panel.forms.comfort_window import ComfortWindowForm
from apps.panel.forms.schedule_failure import form_messages


def test_form_messages_name_each_problem_once() -> None:
    form = ComfortWindowForm(QueryDict('start=06:40&end=07:10'))

    assert form_messages(form) == ('Use a time on the quarter hour: :00, :15, :30 or :45',)
