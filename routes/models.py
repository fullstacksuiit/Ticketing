from django.db import models

from buses.models import Bus
from operators.models import Operator


class Route(models.Model):
    """A source→destination corridor defined by an operator, with boarding
    and dropping points. Trips run on a route on specific dates.

    The route carries the price: `base_fare` is the full-corridor fare an
    operator sets once, and every trip on this route (manual or schedule-
    generated) inherits it. Per-leg overrides live on `SegmentFare`."""

    operator = models.ForeignKey(
        Operator, on_delete=models.CASCADE, related_name="routes"
    )
    source_city = models.CharField(max_length=80)
    destination_city = models.CharField(max_length=80)
    base_fare = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        help_text="Full-corridor fare (source → destination). Trips on this "
        "route inherit it; price individual legs separately under a trip's fares.",
    )
    via_cities = models.CharField(
        max_length=255,
        blank=True,
        help_text="Intermediate cities the bus passes through, comma-separated "
        "in travel order. e.g. Anantapur, Kurnool",
    )
    distance_km = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["source_city", "destination_city"]

    def __str__(self):
        return f"{self.source_city} → {self.destination_city}"

    @property
    def via_list(self):
        """Intermediate cities as a clean list, in travel order."""
        return [c.strip() for c in self.via_cities.split(",") if c.strip()]

    @property
    def stages(self):
        """All cities along the corridor in travel order: source, via…, dest."""
        return [self.source_city, *self.via_list, self.destination_city]

    @property
    def segments(self):
        """Every bookable (from_city, to_city) pair, earlier stage → later."""
        stages = self.stages
        return [
            (stages[i], stages[j])
            for i in range(len(stages))
            for j in range(i + 1, len(stages))
        ]


class Stop(models.Model):
    """A boarding or dropping point on a route, with its time of day."""

    class Kind(models.TextChoices):
        BOARDING = "boarding", "Boarding point"
        DROPPING = "dropping", "Dropping point"
        VIA = "via", "Via / transit point"

    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="stops")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    city = models.CharField(
        max_length=80,
        blank=True,
        help_text="City this point is in — the source, destination, or one of "
        "the route's via cities.",
    )
    name = models.CharField(
        max_length=120,
        blank=True,
        help_text="Specific point name (e.g. a bus stand). Optional for a via "
        "city — leave blank to just record the transit time.",
    )
    address = models.CharField(max_length=200, blank=True)
    time = models.TimeField(help_text="Time of day at this point.")

    class Meta:
        ordering = ["kind", "time"]

    @property
    def label(self):
        """Point name, falling back to the city when unnamed (via points)."""
        return self.name or self.city

    def __str__(self):
        where = f"{self.city} · " if self.city else ""
        return f"{self.get_kind_display()}: {where}{self.label} @ {self.time}"


class Trip(models.Model):
    """A bus running a route at a specific date/time. The fare is a snapshot of
    the route's `base_fare` taken when the trip is created, so a later change to
    the route price leaves already-created (and booked) trips untouched. This is
    the bookable unit passengers search for."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"

    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name="trips")
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="trips")
    departure = models.DateTimeField()
    arrival = models.DateTimeField()
    fare = models.DecimalField(max_digits=8, decimal_places=2)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.SCHEDULED
    )
    schedule = models.ForeignKey(
        "Schedule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trips",
        help_text="The recurring schedule that generated this trip, if any. "
        "Manually-created trips have none.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["departure"]
        constraints = [
            # A schedule produces at most one trip per departure datetime, which
            # makes regeneration idempotent (re-running the generator is a no-op).
            models.UniqueConstraint(
                fields=["schedule", "departure"],
                name="unique_trip_per_schedule_departure",
            )
        ]

    def __str__(self):
        return f"{self.route} · {self.departure:%d %b %H:%M} · {self.bus.name}"

    @property
    def operator(self):
        return self.bus.operator

    @property
    def total_seats(self):
        return self.bus.total_seats

    def fare_for_segment(self, from_city, to_city):
        """Base fare for boarding at `from_city` and alighting at `to_city`.
        Uses the operator's per-segment price if set, else the flat trip fare."""
        sf = self.segment_fares.filter(
            from_city=from_city, to_city=to_city
        ).first()
        return sf.fare if sf else self.fare


class SegmentFare(models.Model):
    """Operator-set fare for one boarding→dropping city segment of a trip, so a
    via leg (e.g. Kurnool → Hyderabad) can be priced apart from the full
    corridor. Pairs without a row fall back to the trip's flat fare."""

    trip = models.ForeignKey(
        Trip, on_delete=models.CASCADE, related_name="segment_fares"
    )
    from_city = models.CharField(max_length=80)
    to_city = models.CharField(max_length=80)
    fare = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        ordering = ["from_city", "to_city"]
        constraints = [
            models.UniqueConstraint(
                fields=["trip", "from_city", "to_city"],
                name="unique_segment_per_trip",
            )
        ]

    def __str__(self):
        return f"{self.from_city} → {self.to_city}: ₹{self.fare}"


class Schedule(models.Model):
    """A recurring template that auto-generates Trips. An operator running the
    same bus on the same route every day (or every other day, or on chosen
    weekdays) sets this up once instead of creating a Trip per date. The
    `generate_trips` command turns active schedules into concrete, bookable
    Trip rows for a rolling window of upcoming days, each priced at the route's
    `base_fare`.

    A daily round trip is just two daily schedules on the same bus — one on the
    onward route, one on the return route, at different times of day."""

    # Python's date.weekday(): Monday is 0 … Sunday is 6.
    WEEKDAY_CHOICES = [
        (0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"),
        (4, "Fri"), (5, "Sat"), (6, "Sun"),
    ]

    class Recurrence(models.TextChoices):
        DAILY = "daily", "Every day"
        ALTERNATE = "alternate", "Alternate days (every 2nd day)"
        WEEKLY = "weekly", "Specific weekdays"

    bus = models.ForeignKey(
        Bus, on_delete=models.CASCADE, related_name="schedules"
    )
    route = models.ForeignKey(
        Route, on_delete=models.CASCADE, related_name="schedules"
    )
    departure_time = models.TimeField(help_text="Time of day the bus departs.")
    arrival_time = models.TimeField(help_text="Time of day the bus arrives.")
    arrival_day_offset = models.PositiveSmallIntegerField(
        default=0,
        help_text="Days after departure that the bus arrives. 0 = same day, "
        "1 = next morning (an overnight trip).",
    )

    recurrence = models.CharField(
        max_length=10, choices=Recurrence.choices, default=Recurrence.DAILY
    )
    weekdays = models.CharField(
        max_length=20,
        blank=True,
        help_text="For 'Specific weekdays' only: comma-separated day numbers, "
        "Mon=0 … Sun=6. e.g. '5,6' for weekends.",
    )
    start_date = models.DateField(
        help_text="First date this schedule can run. Also the parity anchor for "
        "alternate-day schedules (it runs on this date, then every 2nd day)."
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        help_text="Last date this schedule runs. Blank = open-ended.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive schedules generate no new trips. Already-generated "
        "trips are left untouched.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["departure_time"]

    def __str__(self):
        return (
            f"{self.route} · {self.departure_time:%H:%M} · {self.bus.name} "
            f"({self.get_recurrence_display()})"
        )

    @property
    def operator(self):
        return self.bus.operator

    @property
    def weekday_set(self):
        """The chosen weekdays as a set of ints (empty unless WEEKLY)."""
        return {int(x) for x in self.weekdays.split(",") if x.strip().isdigit()}

    @property
    def cadence(self):
        """Human-friendly description of when this schedule runs."""
        if self.recurrence == self.Recurrence.WEEKLY:
            labels = dict(self.WEEKDAY_CHOICES)
            days = ", ".join(labels[d] for d in sorted(self.weekday_set))
            return f"Every {days}" if days else "Weekly (no days set)"
        return self.get_recurrence_display()

    def runs_on(self, d):
        """Whether a trip should exist on date `d` under this schedule."""
        if d < self.start_date:
            return False
        if self.end_date and d > self.end_date:
            return False
        if self.recurrence == self.Recurrence.DAILY:
            return True
        if self.recurrence == self.Recurrence.ALTERNATE:
            return (d - self.start_date).days % 2 == 0
        if self.recurrence == self.Recurrence.WEEKLY:
            return d.weekday() in self.weekday_set
        return False
