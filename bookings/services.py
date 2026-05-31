"""Booking creation. The (trip, seat) unique constraint on BookedSeat is the
source of truth against double-booking — even under concurrent requests, the
second insert fails and we surface a clean error."""

from decimal import Decimal

from django.db import IntegrityError, transaction

from buses.models import Seat

from .models import BookedSeat, Booking


class SeatUnavailable(Exception):
    pass


def booked_seat_ids(trip):
    """IDs of seats already taken on this trip."""
    return set(
        BookedSeat.objects.filter(trip=trip).values_list("seat_id", flat=True)
    )


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
