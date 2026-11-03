from django.db.models import F, Model, QuerySet


def lock_for_update[M: Model](rows: QuerySet[M]) -> int:
    return rows.update(id=F('id'))
