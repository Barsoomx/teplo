from django.db import models

ALIASES_HELP = 'Word stems the bot knows the room by, separated by spaces, e.g. «гостин кухн»'


class Room(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100, unique=True)
    floor = models.ForeignKey('house.Floor', on_delete=models.PROTECT, related_name='rooms')
    position = models.PositiveSmallIntegerField(default=1)
    aliases = models.CharField(max_length=200, blank=True, default='', db_default='', help_text=ALIASES_HELP)

    class Meta:
        ordering = ('floor__level', 'position', 'name')

    def __str__(self) -> str:
        return self.name
