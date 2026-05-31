from decimal import Decimal

from django.db import models

from operators.models import Operator


class Bus(models.Model):
    """A physical bus owned by an operator. Its seat map is made of individual
    Seat rows so we can render a real layout and book seats one by one."""

    class Deck(models.TextChoices):
        LOWER = "lower", "Lower deck"
        UPPER = "upper", "Upper deck"

    operator = models.ForeignKey(
        Operator, on_delete=models.CASCADE, related_name="buses"
    )
    name = models.CharField(max_length=120, help_text="e.g. Volvo 9600 AC Sleeper")
    registration_number = models.CharField(max_length=20)

    is_ac = models.BooleanField(default=True)
    is_sleeper = models.BooleanField(
        default=False, help_text="Sleeper berths vs seater chairs."
    )
    has_upper_deck = models.BooleanField(default=False)

    # Amenities (simple flags; enough for filtering/badges)
    wifi = models.BooleanField(default=False)
    charging_point = models.BooleanField(default=False)
    water_bottle = models.BooleanField(default=False)
    blanket = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Buses"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.registration_number})"

    @property
    def total_seats(self):
        """All physical seats (capacity), reserved or not."""
        return self.seats.filter(is_active=True).count()

    @property
    def bookable_seats(self):
        """Seats the public can actually buy — excludes operator-held seats."""
        return self.seats.filter(is_active=True, is_reserved=False).count()

    @property
    def bus_type_label(self):
        parts = [
            "AC" if self.is_ac else "Non-AC",
            "Sleeper" if self.is_sleeper else "Seater",
        ]
        if self.has_upper_deck:
            parts.append("(2-deck)")
        return " ".join(parts)

    def amenities_list(self):
        out = []
        if self.wifi:
            out.append("WiFi")
        if self.charging_point:
            out.append("Charging")
        if self.water_bottle:
            out.append("Water")
        if self.blanket:
            out.append("Blanket")
        return out


class Seat(models.Model):
    """One seat (or sleeper berth) placed freely on a deck grid. The operator
    builds the layout cell by cell in the seat editor: each seat carries its
    own number, type, price premium, footprint (row_span) and attribute tags,
    so two buses are rarely laid out the same way. Inactive cells represent
    aisle / empty space and are not bookable."""

    class SeatType(models.TextChoices):
        SEATER = "seater", "Seater"
        SLEEPER = "sleeper", "Sleeper"

    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name="seats")
    seat_number = models.CharField(max_length=8)
    deck = models.CharField(
        max_length=10, choices=Bus.Deck.choices, default=Bus.Deck.LOWER
    )
    row = models.PositiveSmallIntegerField()
    col = models.PositiveSmallIntegerField()
    row_span = models.PositiveSmallIntegerField(
        default=1, help_text="Grid rows this seat occupies; 2 = full-length sleeper berth."
    )
    seat_type = models.CharField(
        max_length=10, choices=SeatType.choices, default=SeatType.SEATER
    )
    price_modifier = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Added to the trip's base fare for this seat (e.g. +100 for front/window). May be negative.",
    )

    # Attribute tags — surfaced to passengers as badges / filters.
    is_window = models.BooleanField(default=False)
    is_ladies = models.BooleanField(
        default=False, help_text="Bookable for female passengers only."
    )
    is_reserved = models.BooleanField(
        default=False, help_text="Held by the operator; not sold to the public."
    )

    is_active = models.BooleanField(
        default=True, help_text="Off = aisle / empty space, not bookable."
    )

    class Meta:
        ordering = ["bus", "deck", "row", "col"]
        unique_together = [("bus", "seat_number")]

    def __str__(self):
        return f"{self.bus.name} · {self.seat_number}"

    @property
    def is_bookable(self):
        """A real, sellable seat — active and not held back by the operator."""
        return self.is_active and not self.is_reserved

    def fare_for(self, base_fare):
        """This seat's price on a trip: base fare plus its premium, floored at 0."""
        total = Decimal(base_fare) + self.price_modifier
        return total if total > Decimal("0") else Decimal("0")

    def tags_list(self):
        out = []
        if self.is_window:
            out.append("Window")
        if self.is_ladies:
            out.append("Ladies")
        if self.is_reserved:
            out.append("Reserved")
        return out
