FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir poetry poetry-plugin-export

WORKDIR /code
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root

WORKDIR /code/app
