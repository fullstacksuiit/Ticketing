"""Turning recurring Schedules into concrete, bookable Trips.

`generate_trips_for_schedule` walks a rolling window of upcoming dates and
creates a Trip for each date the schedule runs on. It is idempotent: a Trip is
keyed on (schedule, departure), so re-running never duplicates. This is meant
to be called from the `generate_trips` management command on a daily cron, and
also right after an operator saves a schedule so trips appear immediately."""

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from .models import Trip


def trip_datetimes(schedule, run_date):
    """The aware departure/arrival datetimes for `schedule` on `run_date`.

    Times are interpreted in the project timezone (Asia/Kolkata); the arrival
    can land on a later day via the schedule's arrival_day_offset (overnight)."""
    dep_naive = datetime.combine(run_date, schedule.departure_time)
    arr_date = run_date + timedelta(days=schedule.arrival_day_offset)
    arr_naive = datetime.combine(arr_date, schedule.arrival_time)
    return timezone.make_aware(dep_naive), timezone.make_aware(arr_naive)


@transaction.atomic
def generate_trips_for_schedule(schedule, until_date, today=None):
    """Create any missing Trips for `schedule` from today through `until_date`.

    Never creates trips in the past, and respects the schedule's start/end
    dates and its recurrence rule. Returns the list of newly-created Trips
    (already-existing ones are skipped, so this is safe to run repeatedly)."""
    if not schedule.is_active:
        return []

    today = today or timezone.localdate()
    d = max(schedule.start_date, today)
    created = []
    while d <= until_date:
        if schedule.end_date and d > schedule.end_date:
            break
        if schedule.runs_on(d):
            dep, arr = trip_datetimes(schedule, d)
            trip, was_created = Trip.objects.get_or_create(
                schedule=schedule,
                departure=dep,
                defaults={
                    "bus": schedule.bus,
                    "route": schedule.route,
                    "arrival": arr,
                    "fare": schedule.route.base_fare,
                },
            )
            if was_created:
                created.append(trip)
        d += timedelta(days=1)
    return created


def generate_trips(schedules, days=30, today=None):
    """Generate trips for many schedules across a `days`-wide rolling window.
    Returns a list of (schedule, created_trips) pairs."""
    today = today or timezone.localdate()
    until = today + timedelta(days=days)
    return [
        (s, generate_trips_for_schedule(s, until, today=today)) for s in schedules
    ]
