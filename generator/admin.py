from django.contrib import admin

from .models import GenerateHistory, GenerateVersion, GenerationJob, PublishedPage


class GenerateVersionInline(admin.TabularInline):
    model = GenerateVersion
    extra = 0
    readonly_fields = ("version_no", "note", "created_at")


@admin.register(GenerateHistory)
class GenerateHistoryAdmin(admin.ModelAdmin):
    list_display = ("id", "prompt_preview", "created_at")
    search_fields = ("prompt",)
    readonly_fields = ("created_at",)
    inlines = [GenerateVersionInline]

    @admin.display(description="提示词")
    def prompt_preview(self, obj):
        return obj.prompt[:50]


@admin.register(PublishedPage)
class PublishedPageAdmin(admin.ModelAdmin):
    list_display = ("slug", "version", "created_at")
    search_fields = ("slug",)
    readonly_fields = ("slug", "created_at")


@admin.register(GenerationJob)
class GenerationJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "progress", "project_type", "created_at")
    list_filter = ("status", "project_type")
    search_fields = ("prompt", "client_id")
    readonly_fields = ("id", "created_at", "updated_at")
