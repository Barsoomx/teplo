from django.db import models


class Room(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100, unique=True)
    floor = models.ForeignKey('house.Floor', on_delete=models.PROTECT, related_name='rooms')
    position = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ('floor__level', 'position', 'name')

    def __str__(self) -> str:
        return self.name
