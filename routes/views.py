from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from bookings.cities import INDIAN_CITIES, PRIORITY_CITIES
from operators.decorators import operator_required

from .forms import RouteForm, ScheduleForm, StopForm, TripForm
from .models import Route, Schedule, SegmentFare, Stop, Trip
from .services import generate_trips_for_schedule


def _route_form_context(form, **extra):
    """Shared context for the route add/edit form — includes the city lists
    that power the searchable comboboxes and the via-cities chip picker."""
    return {
        "form": form,
        "cities": INDIAN_CITIES,
        "priority_cities": PRIORITY_CITIES,
        **extra,
    }


def _approved_or_redirect(request):
    op = request.user.operator
    if not op.is_approved:
        messages.error(request, "Your operator account must be approved first.")
        return None
    return op


# ---- Routes ----------------------------------------------------------------

@operator_required
def route_list(request):
    op = request.user.operator
    return render(request, "routes/route_list.html", {"routes": op.routes.all()})


@operator_required
def route_add(request):
    op = _approved_or_redirect(request)
    if op is None:
        return redirect("operator_dashboard")
    if request.method == "POST":
        form = RouteForm(request.POST)
        if form.is_valid():
            route = form.save(commit=False)
            route.operator = op
            route.save()
            messages.success(request, "Route created. Now add boarding & dropping points.")
            return redirect("route_detail", route_id=route.id)
    else:
        form = RouteForm()
    return render(request, "routes/route_form.html", _route_form_context(form))


@operator_required
def route_detail(request, route_id):
    op = request.user.operator
    route = get_object_or_404(Route, id=route_id, operator=op)
    if request.method == "POST":
        form = StopForm(request.POST, route=route)
        if form.is_valid():
            stop = form.save(commit=False)
            stop.route = route
            stop.save()
            messages.success(request, "Stop added.")
            return redirect("route_detail", route_id=route.id)
    else:
        form = StopForm(route=route)
    return render(
        request,
        "routes/route_detail.html",
        {
            "route": route,
            "form": form,
            "boarding": route.stops.filter(kind=Stop.Kind.BOARDING),
            "dropping": route.stops.filter(kind=Stop.Kind.DROPPING),
            "via": route.stops.filter(kind=Stop.Kind.VIA),
        },
    )


@operator_required
def route_edit(request, route_id):
    """Edit an existing route's cities (incl. via) and distance."""
    op = request.user.operator
    route = get_object_or_404(Route, id=route_id, operator=op)
    if request.method == "POST":
        form = RouteForm(request.POST, instance=route)
        if form.is_valid():
            form.save()
            messages.success(request, "Route updated.")
            return redirect("route_detail", route_id=route.id)
    else:
        form = RouteForm(instance=route)
    return render(
        request,
        "routes/route_form.html",
        _route_form_context(form, route=route, editing=True),
    )


@operator_required
def stop_delete(request, stop_id):
    op = request.user.operator
    stop = get_object_or_404(Stop, id=stop_id, route__operator=op)
    route_id = stop.route_id
    if request.method == "POST":
        stop.delete()
        messages.success(request, "Stop removed.")
    return redirect("route_detail", route_id=route_id)


# ---- Trips -----------------------------------------------------------------

@operator_required
def trip_list(request):
    op = request.user.operator
    trips = Trip.objects.filter(bus__operator=op).select_related("bus", "route")
    return render(request, "routes/trip_list.html", {"trips": trips})


@operator_required
def trip_add(request):
    op = _approved_or_redirect(request)
    if op is None:
        return redirect("operator_dashboard")
    if not op.buses.exists() or not op.routes.exists():
        messages.error(request, "Add at least one bus and one route before scheduling a trip.")
        return redirect("trip_list")

    if request.method == "POST":
        form = TripForm(request.POST, operator=op)
        if form.is_valid():
            trip = form.save(commit=False)
            # Price is set on the route; the trip snapshots it at creation.
            trip.fare = trip.route.base_fare
            trip.save()
            messages.success(request, "Trip scheduled and now live for booking.")
            return redirect("trip_list")
    else:
        form = TripForm(operator=op)
    return render(request, "routes/trip_form.html", {"form": form})


@operator_required
def trip_fares(request, trip_id):
    """Per-segment fare table for a trip. Each (boarding → dropping) city pair
    on the route can be priced; pairs left at the default use the flat fare."""
    op = request.user.operator
    trip = get_object_or_404(
        Trip.objects.select_related("route"), id=trip_id, bus__operator=op
    )
    segments = trip.route.segments
    existing = {(s.from_city, s.to_city): s for s in trip.segment_fares.all()}

    if request.method == "POST":
        for i, (frm, to) in enumerate(segments):
            raw = request.POST.get(f"fare_{i}", "").strip()
            if not raw:
                continue
            try:
                value = Decimal(raw)
            except InvalidOperation:
                continue
            if value < 0:
                continue
            SegmentFare.objects.update_or_create(
                trip=trip, from_city=frm, to_city=to, defaults={"fare": value}
            )
        messages.success(request, "Segment fares updated.")
        return redirect("trip_fares", trip_id=trip.id)

    rows = []
    for i, (frm, to) in enumerate(segments):
        sf = existing.get((frm, to))
        rows.append(
            {
                "i": i,
                "from_city": frm,
                "to_city": to,
                "fare": sf.fare if sf else trip.fare,
                "is_default": sf is None,
            }
        )
    return render(request, "routes/trip_fares.html", {"trip": trip, "rows": rows})


# ---- Schedules (recurring trip templates) ----------------------------------

WINDOW_DAYS = 30  # how far ahead schedules auto-generate trips


@operator_required
def schedule_list(request):
    op = request.user.operator
    schedules = Schedule.objects.filter(bus__operator=op).select_related(
        "bus", "route"
    )
    return render(
        request,
        "routes/schedule_list.html",
        {"schedules": schedules, "window_days": WINDOW_DAYS},
    )


@operator_required
def schedule_add(request):
    op = _approved_or_redirect(request)
    if op is None:
        return redirect("operator_dashboard")
    if not op.buses.exists() or not op.routes.exists():
        messages.error(request, "Add at least one bus and one route before creating a schedule.")
        return redirect("schedule_list")

    if request.method == "POST":
        form = ScheduleForm(request.POST, operator=op)
        if form.is_valid():
            schedule = form.save()
            # Generate the rolling window now so trips appear immediately.
            created = generate_trips_for_schedule(
                schedule, timezone.localdate() + timedelta(days=WINDOW_DAYS)
            )
            messages.success(
                request,
                f"Schedule saved — {len(created)} trip(s) generated for the next "
                f"{WINDOW_DAYS} days. They're now live for booking.",
            )
            return redirect("schedule_list")
    else:
        form = ScheduleForm(operator=op)
    return render(request, "routes/schedule_form.html", {"form": form})


@operator_required
def schedule_edit(request, schedule_id):
    op = request.user.operator
    schedule = get_object_or_404(Schedule, id=schedule_id, bus__operator=op)
    if request.method == "POST":
        form = ScheduleForm(request.POST, instance=schedule, operator=op)
        if form.is_valid():
            schedule = form.save()
            created = generate_trips_for_schedule(
                schedule, timezone.localdate() + timedelta(days=WINDOW_DAYS)
            )
            messages.success(
                request,
                f"Schedule updated — {len(created)} new trip(s) generated. "
                "Already-generated trips were left as they were.",
            )
            return redirect("schedule_list")
    else:
        form = ScheduleForm(instance=schedule, operator=op)
    return render(
        request,
        "routes/schedule_form.html",
        {"form": form, "schedule": schedule, "editing": True},
    )


@operator_required
def schedule_toggle(request, schedule_id):
    """Pause or resume a schedule. Pausing stops new trips; existing ones stay."""
    op = request.user.operator
    schedule = get_object_or_404(Schedule, id=schedule_id, bus__operator=op)
    if request.method == "POST":
        schedule.is_active = not schedule.is_active
        schedule.save(update_fields=["is_active"])
        state = "resumed" if schedule.is_active else "paused"
        messages.success(request, f"Schedule {state}.")
    return redirect("schedule_list")
