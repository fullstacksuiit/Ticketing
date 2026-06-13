from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from bookings.models import BookedSeat, Booking
from operators.models import Operator
from routes.models import Route, Trip

from .models import Bus, Seat
from .services import save_layout


def _spec(number, row, col, **extra):
    spec = {
        "number": number,
        "row": row,
        "col": col,
        "row_span": 1,
        "seat_type": Seat.SeatType.SEATER,
        "price_modifier": "0",
        "is_window": False,
        "is_ladies": False,
        "is_reserved": False,
        "is_active": True,
    }
    spec.update(extra)
    return spec


def _layout(*specs):
    return {"decks": {Bus.Deck.LOWER: {"seats": list(specs)}}}


class SaveLayoutTests(TestCase):
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
        )
        self.bus = Bus.objects.create(operator=self.operator, name="Bus 1", registration_number="KA01")

    def _book(self, seat):
        """Create a real BookedSeat referencing `seat` (triggers PROTECT)."""
        route = Route.objects.create(
            operator=self.operator,
            source_city="A",
            destination_city="B",
            base_fare=Decimal("500"),
        )
        now = timezone.now()
        trip = Trip.objects.create(
            bus=self.bus, route=route, departure=now, arrival=now + timedelta(hours=5), fare=Decimal("500")
        )
        booking = Booking.objects.create(
            trip=trip,
            contact_email="p@example.com",
            contact_phone="1",
            total_amount=Decimal("500"),
        )
        return BookedSeat.objects.create(
            booking=booking, trip=trip, seat=seat, passenger_name="P", passenger_age=30, passenger_gender="M", fare=Decimal("500"),
        )

    def test_initial_layout_creates_seats(self):
        n = save_layout(self.bus, _layout(_spec("A1", 1, 1), _spec("A2", 1, 2)))
        self.assertEqual(n, 2)
        self.assertEqual(self.bus.seats.count(), 2)

    def test_unbooked_bus_is_fully_rebuilt(self):
        save_layout(self.bus, _layout(_spec("A1", 1, 1), _spec("A2", 1, 2)))
        save_layout(self.bus, _layout(_spec("B1", 1, 1)))
        self.assertEqual([s.seat_number for s in self.bus.seats.all()], ["B1"])

    def test_booked_seat_survives_edit_and_keeps_booking(self):
        save_layout(self.bus, _layout(_spec("A1", 1, 1), _spec("A2", 1, 2)))
        booked = self._book(self.bus.seats.get(seat_number="A1"))

        # Move A1, add A3, drop the unbooked A2 — all allowed.
        save_layout(self.bus, _layout(
            _spec("A1", 2, 1, price_modifier="100", is_window=True),
            _spec("A3", 1, 1),
        ))

        self.assertEqual(sorted(s.seat_number for s in self.bus.seats.all()), ["A1", "A3"])
        a1 = self.bus.seats.get(seat_number="A1")
        # Same DB row → booking link intact.
        self.assertEqual(a1.id, booked.seat_id)
        self.assertEqual(a1.row, 2)
        self.assertEqual(a1.price_modifier, Decimal("100"))
        self.assertTrue(a1.is_window)
        BookedSeat.objects.get(pk=booked.pk)  # still exists

    def test_removing_booked_seat_detaches_booking(self):
        save_layout(self.bus, _layout(_spec("A1", 1, 1), _spec("A2", 1, 2)))
        booked = self._book(self.bus.seats.get(seat_number="A1"))

        # Remove the booked seat entirely — allowed now.
        save_layout(self.bus, _layout(_spec("A2", 1, 2)))

        self.assertFalse(self.bus.seats.filter(seat_number="A1").exists())
        booked.refresh_from_db()
        self.assertIsNone(booked.seat)  # detached, not deleted
        self.assertEqual(booked.passenger_name, "P")  # booking record intact

    def test_renaming_booked_seat_detaches_booking(self):
        save_layout(self.bus, _layout(_spec("A1", 1, 1)))
        booked = self._book(self.bus.seats.get(seat_number="A1"))

        save_layout(self.bus, _layout(_spec("Z9", 1, 1)))

        self.assertEqual([s.seat_number for s in self.bus.seats.all()], ["Z9"])
        booked.refresh_from_db()
        self.assertIsNone(booked.seat)

    def test_detached_seat_excluded_from_taken(self):
        from bookings.services import booked_seat_ids

        save_layout(self.bus, _layout(_spec("A1", 1, 1)))
        booked = self._book(self.bus.seats.get(seat_number="A1"))
        trip = booked.trip
        save_layout(self.bus, _layout(_spec("Z9", 1, 1)))  # detaches
        self.assertEqual(booked_seat_ids(trip), set())
