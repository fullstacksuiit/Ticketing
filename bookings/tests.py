from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from buses.models import Bus, Seat
from operators.models import Operator
from payments.models import Refund
from routes.models import Route, Trip

from .models import BookedSeat, Booking
from .refunds import quote_refund
from .services import (
    CancellationError,
    RescheduleError,
    booked_seat_ids,
    cancel_booking,
    create_booking,
    reschedule_booking,
    reschedule_eligibility,
)


class CancellationTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="op", password="x", email="op@example.com"
        )
        self.operator = Operator.objects.create(
            user=user,
            company_name="Acme Travels",
            contact_person="A",
            contact_phone="1",
            contact_email="op@example.com",
            status=Operator.Status.APPROVED,
            commission_rate=Decimal("10.00"),
        )
        self.bus = Bus.objects.create(
            operator=self.operator, name="Bus 1", registration_number="KA01"
        )
        self.seat = Seat.objects.create(
            bus=self.bus, seat_number="A1", deck=Bus.Deck.LOWER, row=1, col=1
        )
        self.route = Route.objects.create(
            operator=self.operator,
            source_city="A",
            destination_city="B",
            base_fare=Decimal("500"),
        )

    def _trip(self, *, hours_to_departure):
        depart = timezone.now() + timedelta(hours=hours_to_departure)
        return Trip.objects.create(
            bus=self.bus,
            route=self.route,
            departure=depart,
            arrival=depart + timedelta(hours=5),
            fare=Decimal("500"),
        )

    def _booking(self, trip):
        return create_booking(
            user=None,
            trip=trip,
            passengers=[
                {"seat_id": self.seat.id, "name": "P", "age": 30, "gender": "M"}
            ],
            boarding=None,
            dropping=None,
            contact_email="p@example.com",
            contact_phone="1",
        )

    # ---- policy quoting -----------------------------------------------------

    def test_quote_slabs_by_time_before_departure(self):
        cases = {30: 90, 18: 50, 8: 25, 3: 0}
        for hours, expected_pct in cases.items():
            booking = self._booking(self._trip(hours_to_departure=hours))
            quote = quote_refund(booking)
            self.assertEqual(quote.percent, Decimal(expected_pct), f"{hours}h")
            self.assertEqual(
                quote.amount,
                (booking.total_amount * Decimal(expected_pct) / Decimal("100")).quantize(
                    Decimal("0.01")
                ),
            )
            self.assertTrue(quote.cancellable)

    def test_quote_not_cancellable_after_departure(self):
        booking = self._booking(self._trip(hours_to_departure=-1))
        quote = quote_refund(booking)
        self.assertFalse(quote.cancellable)
        self.assertEqual(quote.percent, Decimal("0"))

    def test_quote_not_cancellable_when_already_cancelled(self):
        booking = self._booking(self._trip(hours_to_departure=30))
        cancel_booking(booking)
        self.assertFalse(quote_refund(booking).cancellable)

    # ---- cancellation effects ----------------------------------------------

    def test_cancel_frees_seats_and_records_refund(self):
        trip = self._trip(hours_to_departure=30)
        booking = self._booking(trip)
        self.assertEqual(booked_seat_ids(trip), {self.seat.id})

        quote = cancel_booking(booking)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertIsNotNone(booking.cancelled_at)
        # Seat is freed — no BookedSeat rows, re-bookable.
        self.assertEqual(BookedSeat.objects.filter(trip=trip).count(), 0)
        self.assertEqual(booked_seat_ids(trip), set())
        # Refund recorded at 90% of 500.
        refund = Refund.objects.get(booking=booking)
        self.assertEqual(refund.amount, Decimal("450.00"))
        self.assertEqual(quote.amount, Decimal("450.00"))

    def test_cancel_resplits_commission_to_retained(self):
        trip = self._trip(hours_to_departure=30)
        booking = self._booking(trip)
        cancel_booking(booking)
        commission = booking.commission
        commission.refresh_from_db()
        # Retained = 500 − 450 = 50; commission 10% → 5, payout 45.
        self.assertEqual(commission.gross_amount, Decimal("50.00"))
        self.assertEqual(commission.commission_amount, Decimal("5.00"))
        self.assertEqual(commission.payout_amount, Decimal("45.00"))

    def test_cancel_already_cancelled_raises(self):
        booking = self._booking(self._trip(hours_to_departure=30))
        cancel_booking(booking)
        with self.assertRaises(CancellationError):
            cancel_booking(booking)

    def test_force_cancels_departed_trip_with_zero_refund(self):
        trip = self._trip(hours_to_departure=-2)
        booking = self._booking(trip)
        # Without force, a departed booking can't be cancelled.
        with self.assertRaises(CancellationError):
            cancel_booking(booking)
        # Admin override proceeds with a 0 refund.
        quote = cancel_booking(booking, force=True)
        self.assertEqual(quote.amount, Decimal("0.00"))
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booked_seat_ids(trip), set())


class RescheduleTests(CancellationTests):
    """Reuses the operator/bus/seat/route fixtures from CancellationTests."""

    def test_eligible_until_cutoff(self):
        # Comfortably ahead of the 6h cutoff → reschedulable.
        self.assertTrue(reschedule_eligibility(self._booking(self._trip(hours_to_departure=30)))[0])
        # Inside the cutoff → not.
        self.assertFalse(reschedule_eligibility(self._booking(self._trip(hours_to_departure=3)))[0])

    def test_cancelled_booking_not_eligible(self):
        booking = self._booking(self._trip(hours_to_departure=30))
        cancel_booking(booking)
        self.assertFalse(reschedule_eligibility(booking)[0])

    def test_reschedule_moves_seats_keeps_price(self):
        old_trip = self._trip(hours_to_departure=30)
        new_trip = self._trip(hours_to_departure=50)
        booking = self._booking(old_trip)
        original_total = booking.total_amount

        reschedule_booking(booking, new_trip=new_trip)

        booking.refresh_from_db()
        self.assertEqual(booking.trip_id, new_trip.id)
        self.assertIsNotNone(booking.rescheduled_at)
        # Same price, no payment/refund side effects.
        self.assertEqual(booking.total_amount, original_total)
        # Old trip's seat is freed; the same seat is now taken on the new trip.
        self.assertEqual(booked_seat_ids(old_trip), set())
        self.assertEqual(booked_seat_ids(new_trip), {self.seat.id})

    def test_reschedule_blocked_when_seat_taken_on_target(self):
        old_trip = self._trip(hours_to_departure=30)
        new_trip = self._trip(hours_to_departure=50)
        booking = self._booking(old_trip)
        # Someone else already holds the only seat on the target trip.
        BookedSeat.objects.create(
            booking=self._booking(self._trip(hours_to_departure=60)),
            trip=new_trip,
            seat=self.seat,
            passenger_name="X",
            passenger_age=20,
            passenger_gender="M",
            fare=Decimal("500"),
        )
        with self.assertRaises(RescheduleError):
            reschedule_booking(booking, new_trip=new_trip)
        # Original booking untouched.
        booking.refresh_from_db()
        self.assertEqual(booking.trip_id, old_trip.id)
        self.assertEqual(booked_seat_ids(old_trip), {self.seat.id})

    def test_reschedule_same_trip_rejected(self):
        trip = self._trip(hours_to_departure=30)
        booking = self._booking(trip)
        with self.assertRaises(RescheduleError):
            reschedule_booking(booking, new_trip=trip)

    def test_reschedule_inside_cutoff_rejected(self):
        booking = self._booking(self._trip(hours_to_departure=3))
        with self.assertRaises(RescheduleError):
            reschedule_booking(booking, new_trip=self._trip(hours_to_departure=50))
