from django.contrib import admin
from django.utils import timezone

from .models import Operator


@admin.register(Operator)
class OperatorAdmin(admin.ModelAdmin):
    list_display = (
        "company_name",
        "status",
        "commission_rate",
        "is_self_operated",
        "city",
        "created_at",
    )
    list_filter = ("status", "is_self_operated")
    list_editable = ("commission_rate", "is_self_operated")
    search_fields = ("company_name", "contact_person", "contact_email")
    readonly_fields = ("created_at", "approved_at")
    actions = ["approve_operators", "suspend_operators"]

    @admin.action(description="Approve selected operators")
    def approve_operators(self, request, queryset):
        updated = queryset.update(
            status=Operator.Status.APPROVED, approved_at=timezone.now()
        )
        self.message_user(request, f"{updated} operator(s) approved.")

    @admin.action(description="Suspend selected operators")
    def suspend_operators(self, request, queryset):
        updated = queryset.update(status=Operator.Status.SUSPENDED)
        self.message_user(request, f"{updated} operator(s) suspended.")
