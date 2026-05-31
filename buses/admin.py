from django.contrib import admin

from .models import Bus, Seat


class SeatInline(admin.TabularInline):
    model = Seat
    extra = 0
    fields = (
        "seat_number", "deck", "row", "col", "row_span", "seat_type",
        "price_modifier", "is_window", "is_ladies", "is_reserved", "is_active",
    )


@admin.register(Bus)
class BusAdmin(admin.ModelAdmin):
    list_display = ("name", "operator", "registration_number", "bus_type_label", "total_seats")
    list_filter = ("operator", "is_ac", "is_sleeper")
    search_fields = ("name", "registration_number")
    inlines = [SeatInline]
