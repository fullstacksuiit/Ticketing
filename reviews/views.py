from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from bookings.models import Booking

from .forms import ReviewForm
from .models import can_review


def _booking_for_request(request, pnr):
    """Fetch a booking by PNR with the same access rule as the ticket page: a
    registered user's booking is scoped to that user (or staff); a guest booking
    is reachable by PNR alone."""
    booking = get_object_or_404(
        Booking.objects.select_related("trip", "trip__bus", "trip__bus__operator"),
        pnr=pnr,
    )
    if booking.user_id:
        is_owner = request.user.is_authenticated and request.user.id == booking.user_id
        if not is_owner and not request.user.is_staff:
            raise Http404
    return booking


def review_create(request, pnr):
    """Leave a verified review for a travelled booking. Eligibility is enforced
    server-side (confirmed, departed, not already reviewed)."""
    booking = _booking_for_request(request, pnr)
    eligible, reason = can_review(booking)
    if not eligible:
        messages.error(request, reason)
        return redirect("ticket", pnr=booking.pnr)

    if request.method == "POST":
        form = ReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.booking = booking
            review.operator = booking.trip.bus.operator
            review.bus = booking.trip.bus
            review.user = booking.user
            review.author_name = (
                booking.user.get_username() if booking.user else "Guest"
            )
            review.save()
            messages.success(request, "Thanks for your review!")
            return redirect("ticket", pnr=booking.pnr)
    else:
        form = ReviewForm()

    return render(
        request,
        "reviews/review_form.html",
        {"booking": booking, "form": form},
    )
