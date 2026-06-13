"""Time-based cancellation refund policy. `quote_refund` is a pure function —
no DB writes — so the ticket page, the confirm page, and the cancel service all
price a cancellation the same way. Change the policy by editing REFUND_SLABS."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import Booking

# (minimum hours before departure, percent of total refunded). Checked top-down;
# the first slab whose threshold the booking still clears wins. Once the bus has
# departed nothing is refundable and the booking can't be cancelled at all.
REFUND_SLABS = [
    (24, 90),
    (12, 50),
    (6, 25),
    (0, 0),
]


@dataclass
class RefundQuote:
    cancellable: bool
    percent: Decimal
    amount: Decimal  # returned to the customer
    retained: Decimal  # cancellation fee the business keeps
    hours_left: float
    note: str = ""
    reason: str = ""  # why it can't be cancelled, when cancellable is False


def _percent_for(hours_left):
    """Refund percent for the time remaining, as a Decimal. 0 once departed."""
    if hours_left <= 0:
        return Decimal("0")
    for min_hours, percent in REFUND_SLABS:
        if hours_left >= min_hours:
            return Decimal(percent)
    return Decimal("0")


def refund_schedule(departure):
    """The cancellation policy expressed as concrete time windows for a given
    departure, so a passenger sees real dates (like RedBus) instead of abstract
    "24h before". Each row is {start, end, percent}; `start` is None on the first
    row (open-ended "before"), and the last row's `end` is the departure itself."""
    rows = []
    upper = None  # hours-before boundary of the previous (more generous) slab
    for min_hours, percent in REFUND_SLABS:
        start = departure - timedelta(hours=upper) if upper is not None else None
        end = departure - timedelta(hours=min_hours)
        rows.append({"start": start, "end": end, "percent": Decimal(percent)})
        upper = min_hours
    return rows


def quote_refund(booking):
    """Price a cancellation of `booking` right now. Pure — never writes."""
    total = booking.total_amount
    hours_left = (booking.trip.departure - timezone.now()).total_seconds() / 3600

    already_cancelled = booking.status != Booking.Status.CONFIRMED
    departed = hours_left <= 0

    percent = _percent_for(hours_left)
    amount = (total * percent / Decimal("100")).quantize(Decimal("0.01"))
    retained = total - amount

    if already_cancelled:
        reason = "This booking can no longer be cancelled."
    elif departed:
        reason = "The bus has already departed — this booking can't be cancelled."
    else:
        reason = ""

    if departed:
        note = "No refund — bus departed"
    else:
        note = f"{percent:.0f}% refund — cancelled {hours_left:.0f}h before departure"

    return RefundQuote(
        cancellable=not already_cancelled and not departed,
        percent=percent,
        amount=amount,
        retained=retained,
        hours_left=hours_left,
        note=note,
        reason=reason,
    )
