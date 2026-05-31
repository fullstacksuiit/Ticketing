import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from operators.decorators import operator_required

from .forms import BusForm
from .models import Bus, Seat
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
    """Serialize a bus's current seats into the layout shape the editor expects."""
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
            }
        )
    return {"decks": decks}


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
