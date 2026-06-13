from django.contrib import admin

from .models import BookedSeat, Booking, SeatHold


class BookedSeatInline(admin.TabularInline):
    model = BookedSeat
    extra = 0


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("pnr", "trip", "user", "total_amount", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("pnr", "contact_email", "contact_phone")
    inlines = [BookedSeatInline]


@admin.register(SeatHold)
class SeatHoldAdmin(admin.ModelAdmin):
    list_display = ("trip", "seat", "session_key", "expires_at", "created_at")
    list_filter = ("trip",)
    search_fields = ("session_key",)
