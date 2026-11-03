import factory

from apps.house.models import Floor, Room


class FloorFactory(factory.django.DjangoModelFactory):
    slug = factory.Sequence(lambda n: f'floor-{n}')
    name = factory.Sequence(lambda n: f'Floor {n}')
    level = factory.Sequence(lambda n: n)

    class Meta:
        model = Floor


class RoomFactory(factory.django.DjangoModelFactory):
    slug = factory.Sequence(lambda n: f'room-{n}')
    name = factory.Sequence(lambda n: f'Room {n}')
    floor = factory.SubFactory(FloorFactory)

    class Meta:
        model = Room
