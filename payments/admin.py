from django.contrib import admin

from .models import Commission, Payment, Refund


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("booking", "amount", "method", "status", "created_at")
    list_filter = ("method", "status")
    search_fields = ("booking__pnr", "gateway_ref")


@admin.register(Commission)
class CommissionAdmin(admin.ModelAdmin):
    list_display = (
        "booking",
        "operator",
        "gross_amount",
        "commission_rate",
        "commission_amount",
        "payout_amount",
        "created_at",
    )
    list_filter = ("operator",)
    search_fields = ("booking__pnr",)


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("booking", "amount", "percent", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("booking__pnr", "gateway_ref")
