from django.db import models


class Floor(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    level = models.SmallIntegerField(unique=True)

    class Meta:
        ordering = ('level',)

    def __str__(self) -> str:
        return self.name
