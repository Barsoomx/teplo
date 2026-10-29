import pytest
from django.contrib.auth.models import User
from django.test import Client


@pytest.fixture
def f_owner() -> User:
    return User.objects.create_user(username='owner', password='owner-password')  # noqa: S106


@pytest.fixture
def f_client(f_owner: User) -> Client:
    client = Client()
    client.force_login(f_owner)
    return client


@pytest.fixture
def f_anonymous_client() -> Client:
    return Client()
