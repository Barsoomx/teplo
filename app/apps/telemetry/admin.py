from django.contrib import admin

from apps.telemetry.models import JournalEntry


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'kind', 'subject')
    list_filter = ('kind',)
    readonly_fields = ('created_at', 'kind', 'subject', 'payload')
