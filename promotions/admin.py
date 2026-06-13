from django.contrib import admin

from .models import Banner


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = (
        "title", "placement", "is_active", "sort_order",
        "starts_at", "ends_at", "created_at",
    )
    list_filter = ("placement", "is_active")
    list_editable = ("is_active", "sort_order")
    search_fields = ("title", "subtitle")
