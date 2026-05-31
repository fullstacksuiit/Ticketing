"""Generate concrete Trips from active recurring Schedules.

Run daily on a cron so a rolling window of upcoming dates always has bookable
trips:

    python manage.py generate_trips            # next 30 days
    python manage.py generate_trips --days 60  # next 60 days
    python manage.py generate_trips --schedule 3   # just one schedule

It is idempotent — re-running only fills in missing dates, never duplicates."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from routes.models import Schedule
from routes.services import generate_trips_for_schedule


class Command(BaseCommand):
    help = "Generate Trips from active recurring Schedules for a rolling window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="How many days ahead to generate trips for (default 30).",
        )
        parser.add_argument(
            "--schedule",
            type=int,
            default=None,
            help="Only generate for this schedule id (default: all active).",
        )

    def handle(self, *args, **options):
        days = options["days"]
        until = timezone.localdate() + timedelta(days=days)

        schedules = Schedule.objects.filter(is_active=True).select_related(
            "bus", "route"
        )
        if options["schedule"] is not None:
            schedules = schedules.filter(id=options["schedule"])

        total = 0
        for schedule in schedules:
            created = generate_trips_for_schedule(schedule, until)
            total += len(created)
            self.stdout.write(f"Schedule #{schedule.id} {schedule}: +{len(created)}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {total} new trip(s) generated through {until:%d %b %Y}."
            )
        )
