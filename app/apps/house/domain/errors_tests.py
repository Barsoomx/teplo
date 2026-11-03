from apps.house.domain.errors import RoomNotFoundError


def test_room_not_found_error_names_the_room() -> None:
    error = RoomNotFoundError('attic')

    assert (error.detail, error.error_code) == ('room not found: attic', 'room_not_found')
