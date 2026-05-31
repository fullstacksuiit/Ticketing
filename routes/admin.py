from datetime import timedelta

from django.contrib import admin
from django.utils import timezone

from .models import Route, Schedule, SegmentFare, Stop, Trip
from .services import generate_trips_for_schedule


class StopInline(admin.TabularInline):
    model = Stop
    extra = 0


class SegmentFareInline(admin.TabularInline):
    model = SegmentFare
    extra = 0


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ("__str__", "operator", "base_fare", "distance_km")
    list_filter = ("operator",)
    inlines = [StopInline]


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ("route", "departure", "arrival", "bus", "fare", "status", "schedule")
    list_filter = ("status", "bus__operator")
    date_hierarchy = "departure"
    inlines = [SegmentFareInline]


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "route", "bus", "departure_time", "cadence", "start_date",
        "end_date", "is_active",
    )
    list_filter = ("is_active", "recurrence", "bus__operator")
    actions = ["generate_now"]

    @admin.action(description="Generate trips for the next 30 days")
    def generate_now(self, request, queryset):
        until = timezone.localdate() + timedelta(days=30)
        total = sum(
            len(generate_trips_for_schedule(s, until)) for s in queryset
        )
        self.message_user(request, f"{total} new trip(s) generated.")
