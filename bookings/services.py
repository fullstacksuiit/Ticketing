"""Booking creation. The (trip, seat) unique constraint on BookedSeat is the
source of truth against double-booking — even under concurrent requests, the
second insert fails and we surface a clean error."""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from buses.models import Seat
from routes.models import Trip

from .models import BookedSeat, Booking, SeatHold

# Rescheduling closes this many hours before the booked departure.
RESCHEDULE_CUTOFF_HOURS = 6


class SeatUnavailable(Exception):
    pass


class CancellationError(Exception):
    pass


class RescheduleError(Exception):
    pass


def booked_seat_ids(trip):
    """IDs of seats already taken on this trip (detached bookings excluded)."""
    return set(
        BookedSeat.objects.filter(trip=trip, seat__isnull=False).values_list(
            "seat_id", flat=True
        )
    )


def purge_expired_holds():
    """Delete every lapsed seat hold. This is the lazy cleanup — called from the
    availability/hold helpers, so no background worker is needed."""
    SeatHold.objects.filter(expires_at__lte=timezone.now()).delete()


def held_seat_ids(trip, exclude_session=None):
    """IDs of seats currently held on this trip, optionally excluding the given
    session (a customer never blocks their own seats). Purges lapsed holds first
    so expired ones never count as taken."""
    purge_expired_holds()
    qs = SeatHold.objects.filter(trip=trip, expires_at__gt=timezone.now())
    if exclude_session:
        qs = qs.exclude(session_key=exclude_session)
    return set(qs.values_list("seat_id", flat=True))


def unavailable_seat_ids(trip, exclude_session=None):
    """Single source of truth for "can't pick this seat": booked outright, or
    actively held by someone else."""
    return booked_seat_ids(trip) | held_seat_ids(trip, exclude_session)


@transaction.atomic
def hold_seats(trip, seat_ids, session_key):
    """Lock `seat_ids` on `trip` for `session_key` until now + SEAT_HOLD_MINUTES.
    Replaces this session's existing holds on the trip so re-selecting is clean.
    Raises SeatUnavailable if a seat is held by another session (the unique
    constraint makes this race-safe). Returns the expiry datetime."""
    purge_expired_holds()
    SeatHold.objects.filter(trip=trip, session_key=session_key).delete()
    expires_at = timezone.now() + timedelta(minutes=settings.SEAT_HOLD_MINUTES)
    try:
        SeatHold.objects.bulk_create(
            [
                SeatHold(
                    trip=trip,
                    seat_id=sid,
                    session_key=session_key,
                    expires_at=expires_at,
                )
                for sid in seat_ids
            ]
        )
    except IntegrityError:
        raise SeatUnavailable("Some seats are no longer available. Please pick again.")
    return expires_at


def release_holds(trip, seat_ids, session_key):
    """Drop this session's holds on the given seats — used once a booking that
    occupied them is confirmed."""
    SeatHold.objects.filter(
        trip=trip, session_key=session_key, seat_id__in=seat_ids
    ).delete()


@transaction.atomic
def create_booking(
    *,
    user,
    trip,
    passengers,
    boarding,
    dropping,
    contact_email,
    contact_phone,
    from_city="",
    to_city="",
    boarding_city="",
    dropping_city="",
    session_key="",
):
    """`passengers` is a list of dicts: {seat_id, name, age, gender}.
    Creates a confirmed booking + a successful dummy payment, or raises
    SeatUnavailable if any seat is taken / invalid. Pricing uses the
    `from_city`→`to_city` segment fare (falling back to the flat trip fare)."""
    from payments.models import Commission, Payment

    seat_ids = [p["seat_id"] for p in passengers]
    if not seat_ids:
        raise SeatUnavailable("Pick at least one seat.")

    # Seats must belong to this trip's bus and be active + not operator-reserved
    valid_seats = {
        s.id: s
        for s in Seat.objects.filter(
            bus=trip.bus, is_active=True, is_reserved=False, id__in=seat_ids
        )
    }
    if len(valid_seats) != len(set(seat_ids)):
        raise SeatUnavailable("One or more selected seats are invalid.")

    # Base fare depends on the city segment booked; each seat adds its premium.
    base = trip.fare_for_segment(from_city, to_city)
    seat_fares = {sid: valid_seats[sid].fare_for(base) for sid in valid_seats}
    total = sum((seat_fares[p["seat_id"]] for p in passengers), Decimal("0"))

    booking = Booking.objects.create(
        user=user,
        trip=trip,
        from_city=from_city,
        to_city=to_city,
        boarding_point=boarding,
        dropping_point=dropping,
        boarding_city=boarding_city,
        dropping_city=dropping_city,
        contact_email=contact_email,
        contact_phone=contact_phone,
        total_amount=total,
        status=Booking.Status.CONFIRMED,
    )

    try:
        BookedSeat.objects.bulk_create(
            [
                BookedSeat(
                    booking=booking,
                    trip=trip,
                    seat_id=p["seat_id"],
                    passenger_name=p["name"],
                    passenger_age=p["age"],
                    passenger_gender=p["gender"],
                    fare=seat_fares[p["seat_id"]],
                )
                for p in passengers
            ]
        )
    except IntegrityError:
        # Another booking grabbed one of these seats first — roll back.
        raise SeatUnavailable("Sorry, one of those seats was just booked. Please pick again.")

    # These seats are now booked outright — drop the customer's checkout holds.
    if session_key:
        release_holds(trip, seat_ids, session_key)

    # Dummy payment — always succeeds for now (Razorpay slots in here later).
    Payment.objects.create(
        booking=booking,
        amount=total,
        method=Payment.Method.DUMMY,
        status=Payment.Status.SUCCESS,
        gateway_ref="DUMMY-OK",
    )

    # Record the money split: platform commission vs operator payout.
    operator = trip.bus.operator
    commission = operator.commission_on(total)
    Commission.objects.create(
        booking=booking,
        operator=operator,
        gross_amount=total,
        commission_rate=operator.effective_commission_rate,
        commission_amount=commission,
        payout_amount=total - commission,
    )
    return booking


@transaction.atomic
def cancel_booking(booking, *, force=False):
    """Cancel a confirmed booking: free its seats, mark it cancelled, record the
    refund, and re-split its commission to the retained (kept) amount so the
    revenue dashboards reflect reality. Refund follows the time-based policy in
    `quote_refund`. `force=True` is the admin override — it cancels even after
    departure (with that policy's 0% refund), but never re-cancels. Returns the
    RefundQuote that was applied."""
    from payments.models import Refund

    from .refunds import quote_refund

    quote = quote_refund(booking)
    if booking.status != Booking.Status.CONFIRMED:
        raise CancellationError("This booking can no longer be cancelled.")
    if not quote.cancellable and not force:
        raise CancellationError(quote.reason)

    # Freeing the seats is just deleting the BookedSeat rows (see its docstring).
    booking.booked_seats.all().delete()

    booking.status = Booking.Status.CANCELLED
    booking.cancelled_at = timezone.now()
    booking.save(update_fields=["status", "cancelled_at"])

    Refund.objects.create(
        booking=booking,
        amount=quote.amount,
        percent=quote.percent,
        policy_note=quote.note,
        status=Refund.Status.PROCESSED,
        gateway_ref="DUMMY-REFUND",
    )

    # The business keeps `retained`; re-split it at the original rate so the
    # operator/admin dashboards (which Sum the Commission rows) stay correct.
    # The refunded portion simply drops out of revenue.
    commission = getattr(booking, "commission", None)
    if commission is not None:
        operator = commission.operator
        retained = quote.retained
        commission.gross_amount = retained
        commission.commission_amount = operator.commission_on(retained)
        commission.payout_amount = retained - commission.commission_amount
        commission.save(
            update_fields=["gross_amount", "commission_amount", "payout_amount"]
        )

    return quote


def reschedule_eligibility(booking):
    """(can_reschedule: bool, reason: str). A booking can move to another
    departure only while it's confirmed and the booked bus is still more than
    RESCHEDULE_CUTOFF_HOURS away. Pure — never writes."""
    if booking.status != Booking.Status.CONFIRMED:
        return False, "Only confirmed bookings can be rescheduled."
    hours_left = (booking.trip.departure - timezone.now()).total_seconds() / 3600
    if hours_left < RESCHEDULE_CUTOFF_HOURS:
        return False, (
            f"Rescheduling closes {RESCHEDULE_CUTOFF_HOURS} hours before departure."
        )
    return True, ""


def match_seat_numbers(trip, seat_numbers):
    """Find an available seat on `trip` for each of `seat_numbers`, matching by
    seat number (so the same seats carry over even if the new trip runs a
    different bus on the route). Returns {seat_number: Seat} when every number
    can be placed — active, not operator-reserved, and not already taken/held —
    else None."""
    unavailable = unavailable_seat_ids(trip)
    found = {}
    for seat in trip.bus.seats.filter(
        seat_number__in=seat_numbers, is_active=True, is_reserved=False
    ):
        if seat.id in unavailable:
            continue
        found.setdefault(seat.seat_number, seat)
    return found if all(n in found for n in seat_numbers) else None


@transaction.atomic
def reschedule_booking(booking, *, new_trip):
    """Move a confirmed booking to `new_trip` on the same route, keeping the same
    seats, passengers, and price (no payment or refund). The seats are freed on
    the old trip and re-taken on the new one inside one transaction; the
    (trip, seat) unique constraint still guards against a concurrent grab.
    Raises RescheduleError if it isn't allowed or the seats can't be placed."""
    ok, reason = reschedule_eligibility(booking)
    if not ok:
        raise RescheduleError(reason)
    if new_trip.id == booking.trip_id:
        raise RescheduleError("Pick a different departure to reschedule to.")
    if new_trip.route_id != booking.trip.route_id:
        raise RescheduleError("You can only move to another departure on the same route.")
    if new_trip.status != Trip.Status.SCHEDULED or new_trip.departure <= timezone.now():
        raise RescheduleError("That departure is no longer available.")

    current = list(booking.booked_seats.select_related("seat"))
    seat_numbers = [bs.seat.seat_number for bs in current if bs.seat]
    if len(seat_numbers) != len(current):
        raise RescheduleError(
            "This booking has an unassigned seat and can't be rescheduled online."
        )

    matched = match_seat_numbers(new_trip, seat_numbers)
    if matched is None:
        raise RescheduleError(
            "Your seats aren't all available on that departure. Please pick another."
        )

    # Free the old trip's seats, then re-take the same seat numbers on the new
    # trip, carrying each passenger and their original fare across unchanged.
    booking.booked_seats.all().delete()
    try:
        BookedSeat.objects.bulk_create(
            [
                BookedSeat(
                    booking=booking,
                    trip=new_trip,
                    seat=matched[bs.seat.seat_number],
                    passenger_name=bs.passenger_name,
                    passenger_age=bs.passenger_age,
                    passenger_gender=bs.passenger_gender,
                    fare=bs.fare,
                )
                for bs in current
            ]
        )
    except IntegrityError:
        raise RescheduleError(
            "One of those seats was just taken. Please pick another departure."
        )

    booking.trip = new_trip
    booking.rescheduled_at = timezone.now()
    booking.save(update_fields=["trip", "rescheduled_at"])
    return booking
