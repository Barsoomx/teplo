from django.contrib.auth.models import User


class PanelUserRepository:
    @staticmethod
    def upsert_superuser(username: str, password: str) -> bool:
        user, created = User.objects.get_or_create(username=username)
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()
        return created
