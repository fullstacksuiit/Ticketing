from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg
from django.utils import timezone

from bookings.models import Booking
from buses.models import Bus
from operators.models import Operator


class Review(models.Model):
    """A verified passenger review. One review per booking — and a booking can
    only be reviewed once its trip has actually departed — so every rating comes
    from someone who really travelled. The operator and bus are snapshotted from
    the booking's trip so aggregates survive even if the trip is later removed."""

    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, related_name="review"
    )
    operator = models.ForeignKey(
        Operator, on_delete=models.CASCADE, related_name="reviews"
    )
    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name="reviews")
    # The reviewer, kept for display/My-bookings; null for guest bookings.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews",
    )
    author_name = models.CharField(max_length=120, blank=True)

    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1 to 5 stars.",
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.rating}★ · {self.booking.pnr}"

    @property
    def display_name(self):
        return self.author_name or (self.user.get_username() if self.user else "Guest")


def rating_for(*, bus=None, operator=None):
    """Average rating + review count for a bus or an operator.

    Returns {"average": float|None, "count": int} — average is None when there
    are no reviews yet, so templates can show "No ratings yet"."""
    qs = Review.objects.all()
    if bus is not None:
        qs = qs.filter(bus=bus)
    if operator is not None:
        qs = qs.filter(operator=operator)
    agg = qs.aggregate(avg=Avg("rating"), count=models.Count("id"))
    avg = agg["avg"]
    return {
        "average": round(avg, 1) if avg is not None else None,
        "count": agg["count"],
    }


def can_review(booking):
    """Whether `booking` is eligible for a verified review: it must be a
    confirmed booking whose trip has already departed, and not yet reviewed.
    Returns (eligible: bool, reason: str)."""
    if booking.status != Booking.Status.CONFIRMED:
        return False, "Only confirmed bookings can be reviewed."
    if booking.trip.departure > timezone.now():
        return False, "You can review this trip after you've travelled."
    if Review.objects.filter(booking=booking).exists():
        return False, "You've already reviewed this trip."
    return True, ""
