from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from routes.models import Stop, Trip

from .cities import INDIAN_CITIES, PRIORITY_CITIES
from .models import Booking
from .services import SeatUnavailable, booked_seat_ids, create_booking


def _resolve_segment(trip, src, dst):
    """Map free-text from/to search terms onto two stages of this trip's route,
    returning (from_city, to_city). Unmatched terms default to the corridor
    ends, and an out-of-order match falls back to the full route."""
    stages = trip.route.stages

    def find(term, default):
        if term:
            for i, city in enumerate(stages):
                if term.lower() in city.lower():
                    return i
        return default

    i = find(src, 0)
    j = find(dst, len(stages) - 1)
    if i >= j:
        i, j = 0, len(stages) - 1
    return stages[i], stages[j]


def _point_groups(trip, kinds, cities):
    """Boarding/dropping options grouped by city for the booked segment. Each
    city offers its operator-defined points with times — boarding/dropping
    points plus via/transit points, which serve as both — or, when a city has
    none, a city-level fallback so every via city is still selectable."""
    by_city = defaultdict(list)
    for s in trip.route.stops.filter(kind__in=kinds).order_by("time"):
        by_city[s.city].append(s)
    groups = []
    for c in cities:
        timed = by_city.get(c, [])
        if timed:
            options = [
                {"value": f"stop:{s.id}", "label": f"{s.label} — {s.time.strftime('%H:%M')}", "city": c}
                for s in timed
            ]
        else:
            options = [{"value": f"city:{c}", "label": f"{c} (any point)", "city": c}]
        groups.append({"city": c, "options": options})
    return groups


def _resolve_point(trip, raw):
    """Turn a posted boarding/dropping value into (Stop|None, city). Accepts
    "stop:<id>" for a named point or "city:<name>" for a city-level pick."""
    raw = (raw or "").strip()
    if raw.startswith("stop:"):
        stop = Stop.objects.filter(id=raw[5:], route=trip.route).first()
        return stop, (stop.city if stop else "")
    if raw.startswith("city:"):
        return None, raw[5:]
    return None, ""


def search(request):
    """from / to / date → matching scheduled trips with seats left."""
    src = request.GET.get("from", "").strip()
    dst = request.GET.get("to", "").strip()
    date_str = request.GET.get("date", "").strip()

    trips = []
    searched = bool(src or dst or date_str)
    if searched:
        qs = Trip.objects.filter(status=Trip.Status.SCHEDULED).select_related(
            "bus", "bus__operator", "route"
        )
        if src:
            qs = qs.filter(
                Q(route__source_city__icontains=src)
                | Q(route__via_cities__icontains=src)
            )
        if dst:
            qs = qs.filter(
                Q(route__destination_city__icontains=dst)
                | Q(route__via_cities__icontains=dst)
            )
        if date_str:
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d").date()
                qs = qs.filter(departure__date=d)
            except ValueError:
                pass
        else:
            qs = qs.filter(departure__gte=timezone.now())

        qs = qs.annotate(taken=Count("booked_seats")).prefetch_related(
            "segment_fares"
        )
        for t in qs:
            t.seats_left = t.bus.bookable_seats - t.taken
            # Price the leg the passenger searched for, not the whole corridor.
            t.seg_from, t.seg_to = _resolve_segment(t, src, dst)
            t.seg_fare = t.fare_for_segment(t.seg_from, t.seg_to)
            trips.append(t)

    return render(
        request,
        "bookings/search_results.html",
        {
            "trips": trips,
            "src": src,
            "dst": dst,
            "date": date_str,
            "searched": searched,
            "cities": INDIAN_CITIES,
            "priority_cities": PRIORITY_CITIES,
        },
    )


def _seat_grid(trip, base_fare):
    """Seats grouped by deck for CSS-grid rendering. Each seat is annotated
    with its trip fare (the segment base fare plus its premium) and whether
    it's booked. Each deck also carries its column count so the template can
    lay out a grid that honours free seat placement and sleeper row-spans."""
    taken = booked_seat_ids(trip)
    by_deck = defaultdict(list)
    cols = defaultdict(int)
    for seat in trip.bus.seats.filter(is_active=True):
        seat.is_booked = seat.id in taken
        seat.trip_fare = seat.fare_for(base_fare)
        by_deck[seat.deck].append(seat)
        cols[seat.deck] = max(cols[seat.deck], seat.col)
    return [
        {"deck": deck, "cols": cols[deck], "seats": by_deck[deck]}
        for deck in sorted(by_deck)
    ]


def trip_seats(request, trip_id):
    trip = get_object_or_404(
        Trip.objects.select_related("bus", "route"), id=trip_id, status=Trip.Status.SCHEDULED
    )

    # The city segment being booked drives the base fare. It rides along from
    # search via the seat form (POST) or the search link (GET).
    getter = request.POST if request.method == "POST" else request.GET
    from_city, to_city = _resolve_segment(
        trip, getter.get("from", ""), getter.get("to", "")
    )
    base_fare = trip.fare_for_segment(from_city, to_city)

    if request.method == "POST":
        seat_ids = request.POST.getlist("seats")
        if not seat_ids:
            messages.error(request, "Please select at least one seat.")
            return redirect("trip_seats", trip_id=trip.id)
        # Re-check availability before showing the passenger form. Reserved
        # seats are operator-held and never bookable by the public.
        taken = booked_seat_ids(trip)
        chosen = list(
            trip.bus.seats.filter(
                id__in=seat_ids, is_active=True, is_reserved=False
            ).exclude(id__in=taken)
        )
        if len(chosen) != len(seat_ids):
            messages.error(request, "Some seats are no longer available. Please pick again.")
            return redirect("trip_seats", trip_id=trip.id)
        total = Decimal("0")
        for seat in chosen:
            seat.trip_fare = seat.fare_for(base_fare)
            total += seat.trip_fare
        # Offer boarding/dropping across the booked segment's cities: board
        # from the origin up to the last-but-one stage, drop from the next
        # stage through the destination.
        stages = trip.route.stages
        i = stages.index(from_city) if from_city in stages else 0
        j = stages.index(to_city) if to_city in stages else len(stages) - 1
        if i >= j:
            i, j = 0, len(stages) - 1
        segment_stages = stages[i : j + 1]
        # Fare for every selectable boarding→dropping pair, so the page can
        # reprice live as the passenger changes their stops.
        fare_map = {
            f"{a}|{b}": str(trip.fare_for_segment(a, b))
            for x, a in enumerate(segment_stages)
            for b in segment_stages[x + 1 :]
        }
        return render(
            request,
            "bookings/passenger_details.html",
            {
                "trip": trip,
                "seats": chosen,
                "from_city": from_city,
                "to_city": to_city,
                "boarding": _point_groups(
                    trip, (Stop.Kind.BOARDING, Stop.Kind.VIA), stages[i:j]
                ),
                "dropping": _point_groups(
                    trip, (Stop.Kind.DROPPING, Stop.Kind.VIA), stages[i + 1 : j + 1]
                ),
                "total": total,
                "fare_map": fare_map,
                "seat_premiums": {
                    str(seat.id): str(seat.price_modifier) for seat in chosen
                },
            },
        )

    return render(
        request,
        "bookings/seat_map.html",
        {
            "trip": trip,
            "decks": _seat_grid(trip, base_fare),
            "from_city": from_city,
            "to_city": to_city,
            "base_fare": base_fare,
        },
    )


def trip_book(request, trip_id):
    """Final submit: passenger details + dummy payment → confirmed booking.
    No login required — guests book by giving a contact email/phone."""
    trip = get_object_or_404(Trip, id=trip_id, status=Trip.Status.SCHEDULED)
    if request.method != "POST":
        return redirect("trip_seats", trip_id=trip.id)

    seat_ids = request.POST.getlist("seat_id")
    passengers = []
    for sid in seat_ids:
        passengers.append(
            {
                "seat_id": int(sid),
                "name": request.POST.get(f"name_{sid}", "").strip(),
                "age": request.POST.get(f"age_{sid}") or 0,
                "gender": request.POST.get(f"gender_{sid}", "O"),
            }
        )
    if any(not p["name"] for p in passengers):
        messages.error(request, "Please enter a name for every passenger.")
        return redirect("trip_seats", trip_id=trip.id)

    contact_email = request.POST.get("contact_email", "").strip()
    contact_phone = request.POST.get("contact_phone", "").strip()
    if not contact_email:
        messages.error(request, "Please enter a contact email so we can send your ticket.")
        return redirect("trip_seats", trip_id=trip.id)

    # A boarding/dropping value is either "stop:<id>" (a named point) or
    # "city:<name>" (a via city with no named point). Resolve to (Stop, city).
    boarding, boarding_city = _resolve_point(trip, request.POST.get("boarding"))
    dropping, dropping_city = _resolve_point(trip, request.POST.get("dropping"))

    # The fare follows the segment the passenger actually boards/alights, so
    # price by the chosen cities. Fall back to the searched segment only if the
    # pick is missing or out of order along the route.
    stages = trip.route.stages
    from_city, to_city = _resolve_segment(
        trip, request.POST.get("from", ""), request.POST.get("to", "")
    )
    if (
        boarding_city in stages
        and dropping_city in stages
        and stages.index(boarding_city) < stages.index(dropping_city)
    ):
        from_city, to_city = boarding_city, dropping_city

    try:
        booking = create_booking(
            user=request.user if request.user.is_authenticated else None,
            trip=trip,
            passengers=passengers,
            boarding=boarding,
            dropping=dropping,
            contact_email=contact_email,
            contact_phone=contact_phone,
            from_city=from_city,
            to_city=to_city,
            boarding_city=boarding_city,
            dropping_city=dropping_city,
        )
    except SeatUnavailable as e:
        messages.error(request, str(e))
        return redirect("trip_seats", trip_id=trip.id)

    messages.success(request, "Payment successful — your ticket is confirmed!")
    return redirect("ticket", pnr=booking.pnr)


def _booking_for_request(request, pnr):
    """Fetch a booking by PNR, enforcing access: a registered user's ticket is
    scoped to that user (or staff); a guest booking is reachable by PNR alone."""
    booking = get_object_or_404(
        Booking.objects.select_related("trip", "trip__bus", "trip__bus__operator", "trip__route"),
        pnr=pnr,
    )
    if booking.user_id:
        is_owner = request.user.is_authenticated and request.user.id == booking.user_id
        if not is_owner and not request.user.is_staff:
            raise Http404
    return booking


def ticket(request, pnr):
    """Viewable by PNR, subject to the access rules in `_booking_for_request`."""
    booking = _booking_for_request(request, pnr)
    return render(request, "bookings/ticket.html", {"booking": booking})


def ticket_pdf(request, pnr):
    """Download the e-ticket as a PDF."""
    from .pdf import build_ticket_pdf

    booking = _booking_for_request(request, pnr)
    pdf = build_ticket_pdf(booking)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="BusGo-{booking.pnr}.pdf"'
    return response


@login_required
def my_bookings(request):
    bookings = request.user.bookings.select_related("trip", "trip__route").all()
    return render(request, "bookings/my_bookings.html", {"bookings": bookings})


def find_booking(request):
    """Look up a booking by PNR + the contact email it was booked with — for
    guests (and anyone) to retrieve a ticket without an account. The email acts
    as the credential, so this renders the ticket directly on a match."""
    error = None
    if request.method == "POST":
        pnr = request.POST.get("pnr", "").strip().upper()
        email = request.POST.get("email", "").strip()
        booking = (
            Booking.objects.select_related("trip", "trip__bus", "trip__route")
            .filter(pnr=pnr, contact_email__iexact=email)
            .first()
        )
        if booking:
            return render(request, "bookings/ticket.html", {"booking": booking})
        error = "No booking found for that PNR and email. Please check and try again."
    return render(request, "bookings/find_booking.html", {"error": error})
