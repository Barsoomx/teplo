from django.contrib import admin
from django.http import HttpRequest

from apps.power.models import Load, LoadSettings, Outage

SHED_IDENTITY_FIELDS = ('slug', 'kind', 'zone', 'device')


@admin.register(Outage)
class OutageAdmin(admin.ModelAdmin):
    list_display = ('began_at', 'ended_at', 'cause', 'cause_marked_at')
    list_filter = ('cause',)

    def has_delete_permission(self, request: HttpRequest, obj: Outage | None = None) -> bool:
        return (obj is None or obj.boot_id is None) and super().has_delete_permission(request, obj)


@admin.register(Load)
class LoadAdmin(admin.ModelAdmin):
    list_display = (
        'priority',
        'name',
        'kind',
        'zone',
        'device',
        'phase',
        'nominal_amps',
        'learned_phase',
        'learned_amps',
        'learning_samples',
        'live',
    )
    list_display_links = ('name',)
    list_editable = ('phase', 'nominal_amps', 'live')
    readonly_fields = (
        'learned_phase',
        'learned_amps',
        'learning_samples',
        'shed_at',
        'shed_reason',
        'shed_phase',
        'shed_was_on',
        'shed_device',
        'shed_handed_back',
    )

    def get_readonly_fields(self, request: HttpRequest, obj: Load | None = None) -> tuple[str, ...]:
        fields = tuple(super().get_readonly_fields(request, obj))
        if not _shed_live(obj):
            return fields

        return (*fields, *SHED_IDENTITY_FIELDS)

    def has_delete_permission(self, request: HttpRequest, obj: Load | None = None) -> bool:
        return not _shed_live(obj) and super().has_delete_permission(request, obj)


@admin.register(LoadSettings)
class LoadSettingsAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'load_management_live', 'start_ceiling_amps', 'shed_amps', 'restore_amps')

    def has_add_permission(self, request: HttpRequest) -> bool:  # noqa: ARG002
        return not LoadSettings.objects.exists()

    def has_delete_permission(self, request: HttpRequest, obj: LoadSettings | None = None) -> bool:  # noqa: ARG002
        return False


def _shed_live(load: Load | None) -> bool:
    return load is not None and load.shed_at is not None and not load.shed_handed_back
