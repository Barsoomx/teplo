from apps.panel.converters import WeekdayConverter


def test_weekday_converter_reads_and_writes_a_weekday_number() -> None:
    converter = WeekdayConverter()

    assert (converter.regex, converter.to_python('6'), converter.to_url(0)) == ('[0-6]', 6, '0')
