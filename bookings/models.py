import random
import string

from django.conf import settings
from django.db import models

from buses.models import Seat
from routes.models import Stop, Trip


def generate_pnr():
    """A short, human-friendly booking reference, e.g. BG7K2QX9."""
    return "BG" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


class Booking(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending payment"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    pnr = models.CharField(max_length=10, unique=True, default=generate_pnr, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="bookings"
    )
    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, related_name="bookings")

    # The city segment actually booked (may be a via leg, not the full route).
    from_city = models.CharField(max_length=80, blank=True)
    to_city = models.CharField(max_length=80, blank=True)

    # A boarding/dropping selection is either a named operator-defined Stop, or
    # a city-level pick (a via city with no named point yet). The *_city fields
    # always hold the city; the FK is set only when a named point was chosen.
    boarding_point = models.ForeignKey(
        Stop, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    dropping_point = models.ForeignKey(
        Stop, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    boarding_city = models.CharField(max_length=80, blank=True)
    dropping_city = models.CharField(max_length=80, blank=True)

    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=15)

    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Set when the booking is moved to a different departure (same route, same
    # seats, same price). Its presence marks the ticket as rescheduled.
    rescheduled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.pnr} · {self.trip.route}"

    @property
    def operator(self):
        return self.trip.bus.operator

    @property
    def seat_numbers(self):
        return ", ".join(
            bs.seat.seat_number if bs.seat else "—" for bs in self.booked_seats.all()
        )

    @property
    def journey_from(self):
        """Boarding city for this booking, defaulting to the route source."""
        return self.from_city or self.trip.route.source_city

    @property
    def journey_to(self):
        """Alighting city for this booking, defaulting to the route dest."""
        return self.to_city or self.trip.route.destination_city

    @property
    def boarding_label(self):
        """City + named point if there is one, else just the city."""
        if self.boarding_point:
            city = self.boarding_point.city or self.boarding_city
            return f"{city} · {self.boarding_point.name}" if city else self.boarding_point.name
        return self.boarding_city

    @property
    def dropping_label(self):
        if self.dropping_point:
            city = self.dropping_point.city or self.dropping_city
            return f"{city} · {self.dropping_point.name}" if city else self.dropping_point.name
        return self.dropping_city


class BookedSeat(models.Model):
    """One seat on one trip held by a booking. A row existing here means the
    seat is taken for that trip — the unique constraint blocks double-booking.
    Cancelling a booking deletes these rows, freeing the seats."""

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="booked_seats")
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="booked_seats")
    # SET_NULL (not PROTECT): an operator may redesign a bus's seat map after
    # sales start. Removing a booked seat detaches it here — the passenger keeps
    # the booking but loses the seat assignment until it's reassigned.
    seat = models.ForeignKey(
        Seat, on_delete=models.SET_NULL, null=True, related_name="bookings"
    )

    passenger_name = models.CharField(max_length=120)
    passenger_age = models.PositiveSmallIntegerField()
    passenger_gender = models.CharField(
        max_length=1, choices=[("M", "Male"), ("F", "Female"), ("O", "Other")]
    )
    fare = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["trip", "seat"], name="unique_seat_per_trip")
        ]

    def __str__(self):
        seat = self.seat.seat_number if self.seat else "—"
        return f"{seat} · {self.passenger_name}"


class SeatHold(models.Model):
    """A temporary lock on a seat while a customer is at checkout. Holds are
    keyed by the browser session, expire after a few minutes, and are deleted
    once the booking confirms (or lazily once they lapse). The (trip, seat)
    unique constraint makes concurrent holds race-safe — the second inserter
    fails — closing the window where two customers pick the same seat before
    either has paid. BookedSeat remains the ultimate double-booking guard."""

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="seat_holds")
    seat = models.ForeignKey(Seat, on_delete=models.CASCADE, related_name="holds")
    session_key = models.CharField(max_length=40)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["trip", "seat"], name="unique_hold_per_trip")
        ]

    def __str__(self):
        return f"hold {self.seat.seat_number} · {self.session_key[:8]}"
