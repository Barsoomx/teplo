class WeekdayConverter:
    regex = '[0-6]'

    def to_python(self, value: str) -> int:
        return int(value)

    def to_url(self, value: int) -> str:
        return str(value)
