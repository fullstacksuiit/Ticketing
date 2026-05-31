from django.contrib import admin

from .models import BookedSeat, Booking


class BookedSeatInline(admin.TabularInline):
    model = BookedSeat
    extra = 0


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("pnr", "trip", "user", "total_amount", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("pnr", "contact_email", "contact_phone")
    inlines = [BookedSeatInline]
