import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from operators.decorators import operator_required

from .forms import BusForm
from .models import Bus, BusPhoto, Seat
from .services import LayoutError, save_layout


def _require_approved(request):
    """Operators must be approved before managing inventory."""
    op = request.user.operator
    if not op.is_approved:
        messages.error(request, "Your operator account must be approved first.")
        return None
    return op


@operator_required
def bus_list(request):
    op = request.user.operator
    return render(request, "buses/bus_list.html", {"buses": op.buses.all(), "operator": op})


@operator_required
def bus_add(request):
    op = _require_approved(request)
    if op is None:
        return redirect("operator_dashboard")

    if request.method == "POST":
        form = BusForm(request.POST)
        if form.is_valid():
            bus = form.save(commit=False)
            bus.operator = op
            bus.save()
            messages.success(request, "Bus added. Now lay out its seats.")
            return redirect("seat_editor", bus_id=bus.id)
    else:
        form = BusForm()
    return render(request, "buses/bus_form.html", {"form": form})


def _layout_json(bus):
    """Serialize a bus's current seats into the layout shape the editor expects.

    `is_booked` flags seats that already have bookings: the editor locks these
    so the operator can't remove or rename them (save_layout would reject it)."""
    booked_ids = set(
        bus.seats.filter(bookings__isnull=False).values_list("id", flat=True)
    )
    decks = {Bus.Deck.LOWER: {"seats": []}, Bus.Deck.UPPER: {"seats": []}}
    for s in bus.seats.all():
        decks.setdefault(s.deck, {"seats": []})["seats"].append(
            {
                "number": s.seat_number,
                "row": s.row,
                "col": s.col,
                "row_span": s.row_span,
                "seat_type": s.seat_type,
                "price_modifier": str(s.price_modifier),
                "is_window": s.is_window,
                "is_ladies": s.is_ladies,
                "is_reserved": s.is_reserved,
                "is_active": s.is_active,
                "is_booked": s.id in booked_ids,
            }
        )
    return {"decks": decks}


@operator_required
def bus_photos(request, bus_id):
    """Manage a bus's photo gallery — passengers see these before booking.
    POST with files appends photos; POST with `delete` removes one."""
    op = request.user.operator
    bus = get_object_or_404(Bus, id=bus_id, operator=op)

    if request.method == "POST":
        delete_id = request.POST.get("delete")
        if delete_id:
            bus.photos.filter(id=delete_id).delete()
            messages.success(request, "Photo removed.")
            return redirect("bus_photos", bus_id=bus.id)

        images = request.FILES.getlist("images")
        if not images:
            messages.error(request, "Please choose at least one image.")
            return redirect("bus_photos", bus_id=bus.id)
        # New photos go to the back of the gallery, after any existing ones.
        start = bus.photos.count()
        for i, img in enumerate(images):
            BusPhoto.objects.create(bus=bus, image=img, sort_order=start + i)
        messages.success(request, f"Added {len(images)} photo(s).")
        return redirect("bus_photos", bus_id=bus.id)

    return render(request, "buses/bus_photos.html", {"bus": bus})


@operator_required
def seat_editor(request, bus_id):
    op = request.user.operator
    bus = get_object_or_404(Bus, id=bus_id, operator=op)

    if request.method == "POST":
        try:
            layout = json.loads(request.POST.get("layout", ""))
        except (ValueError, TypeError):
            messages.error(request, "Couldn't read the layout — please try again.")
            return redirect("seat_editor", bus_id=bus.id)
        try:
            n = save_layout(bus, layout)
        except LayoutError as e:
            messages.error(request, str(e))
            return redirect("seat_editor", bus_id=bus.id)
        messages.success(request, f"Seat layout saved — {n} seats.")
        return redirect("bus_list")

    return render(
        request,
        "buses/seat_editor.html",
        {
            "bus": bus,
            # Pass the dict — the template's |json_script does the JSON encoding.
            "layout": _layout_json(bus),
            "seat_types": Seat.SeatType.choices,
        },
    )
