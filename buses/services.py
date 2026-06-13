"""Seat-map building. A bus starts with no seats; `save_layout` stores
whatever custom layout the operator designs seat-by-seat in the interactive
drag & drop seat editor."""

from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import Bus, Seat


class LayoutError(Exception):
    """The submitted layout is malformed or violates a constraint."""


VALID_TYPES = {Seat.SeatType.SEATER, Seat.SeatType.SLEEPER}


def _seat_from_spec(bus, deck, spec):
    """Validate one seat dict from the editor and build a Seat (unsaved)."""
    number = str(spec.get("number", "")).strip()
    if not number:
        raise LayoutError("Every seat needs a number/label.")
    if len(number) > 8:
        raise LayoutError(f"Seat label '{number}' is too long (max 8 characters).")

    try:
        row = int(spec["row"])
        col = int(spec["col"])
        row_span = int(spec.get("row_span", 1))
    except (KeyError, TypeError, ValueError):
        raise LayoutError("A seat has an invalid grid position.")
    if row < 1 or col < 1 or row_span < 1 or row_span > 4:
        raise LayoutError("A seat has an out-of-range position or size.")

    seat_type = spec.get("seat_type", Seat.SeatType.SEATER)
    if seat_type not in VALID_TYPES:
        raise LayoutError(f"Unknown seat type '{seat_type}'.")

    try:
        price_modifier = Decimal(str(spec.get("price_modifier", "0") or "0"))
    except (InvalidOperation, TypeError):
        raise LayoutError(f"Seat '{number}' has an invalid price.")

    return Seat(
        bus=bus,
        seat_number=number,
        deck=deck,
        row=row,
        col=col,
        row_span=row_span,
        seat_type=seat_type,
        price_modifier=price_modifier,
        is_window=bool(spec.get("is_window")),
        is_ladies=bool(spec.get("is_ladies")),
        is_reserved=bool(spec.get("is_reserved")),
        is_active=True,
    )


def _aisle_from_spec(bus, deck, spec, label):
    """Build an inactive 'seat' that represents a walkway/gap cell. Aisles
    occupy a grid position (for overlap checks) but are never bookable and
    don't appear on the passenger seat map."""
    try:
        row = int(spec["row"])
        col = int(spec["col"])
    except (KeyError, TypeError, ValueError):
        raise LayoutError("An aisle cell has an invalid grid position.")
    if row < 1 or col < 1:
        raise LayoutError("An aisle cell has an out-of-range position.")
    return Seat(
        bus=bus,
        seat_number=label,
        deck=deck,
        row=row,
        col=col,
        row_span=1,
        seat_type=Seat.SeatType.SEATER,
        is_active=False,
    )


@transaction.atomic
def save_layout(bus, layout):
    """Save a bus's seat map from a layout designed in the editor.

    `layout` is the parsed JSON payload:
        {"decks": {"lower": {"seats": [<seat spec>, ...]},
                   "upper": {"seats": [...]}}}
    Each seat spec has number, row, col, row_span, seat_type, price_modifier,
    is_window, is_ladies, is_reserved. Seat numbers must be unique across the
    whole bus. `bus.has_upper_deck` is set from whether an upper deck has seats.

    The layout is reconciled by seat label rather than rebuilt wholesale: a seat
    whose label survives the edit keeps its database row (and any bookings linked
    to it), and is updated in place. A seat the operator removes (or renames) is
    deleted; because BookedSeat.seat is SET_NULL, any booking on it is detached
    (kept, but with no seat) rather than blocking the edit.

    Raises LayoutError (rolling back) on any invalid input."""
    decks = (layout or {}).get("decks") or {}
    if not isinstance(decks, dict):
        raise LayoutError("Malformed layout.")

    new_seats = []
    seen_numbers = set()
    upper_has_seats = False
    aisle_n = 0  # auto-numbers walkway cells, which carry no real label
    real_seat_count = 0

    for deck_key, deck_data in decks.items():
        if deck_key not in {Bus.Deck.LOWER, Bus.Deck.UPPER}:
            raise LayoutError(f"Unknown deck '{deck_key}'.")
        specs = (deck_data or {}).get("seats") or []
        occupied = set()
        for spec in specs:
            is_aisle = spec.get("is_active") is False
            if is_aisle:
                # A walkway/gap cell: not bookable, no operator-facing label.
                aisle_n += 1
                label = f"·{aisle_n}"
                while label in seen_numbers:
                    aisle_n += 1
                    label = f"·{aisle_n}"
                seat = _aisle_from_spec(bus, deck_key, spec, label)
            else:
                seat = _seat_from_spec(bus, deck_key, spec)
                if seat.seat_number in seen_numbers:
                    raise LayoutError(f"Duplicate seat label '{seat.seat_number}'.")
                real_seat_count += 1
                if deck_key == Bus.Deck.UPPER:
                    upper_has_seats = True
            seen_numbers.add(seat.seat_number)
            # Footprint-overlap check within the deck (seats and aisles alike).
            for r in range(seat.row, seat.row + seat.row_span):
                cell = (r, seat.col)
                if cell in occupied:
                    raise LayoutError("Two cells overlap in the layout.")
                occupied.add(cell)
            new_seats.append(seat)

    if real_seat_count == 0:
        raise LayoutError("A bus needs at least one seat.")

    # Reconcile against the current seats by label. Seats whose label survives
    # the edit keep their row (and bookings) and are updated in place; removed
    # ones are deleted (SET_NULL detaches any booking); new ones are created.
    existing = {s.seat_number: s for s in bus.seats.all()}
    new_numbers = {s.seat_number for s in new_seats}
    bus.seats.exclude(seat_number__in=new_numbers).delete()

    to_create = []
    for seat in new_seats:
        current = existing.get(seat.seat_number)
        if current is None:
            to_create.append(seat)
            continue
        current.deck = seat.deck
        current.row = seat.row
        current.col = seat.col
        current.row_span = seat.row_span
        current.seat_type = seat.seat_type
        current.price_modifier = seat.price_modifier
        current.is_window = seat.is_window
        current.is_ladies = seat.is_ladies
        current.is_reserved = seat.is_reserved
        current.is_active = seat.is_active
        current.save()
    Seat.objects.bulk_create(to_create)

    bus.has_upper_deck = upper_has_seats
    bus.save(update_fields=["has_upper_deck"])
    return len(new_seats)
