from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Sum
from django.shortcuts import redirect, render

from .decorators import operator_required
from .forms import OperatorProfileForm
from .models import Operator


@login_required
def onboarding(request):
    """Create or edit the operator company profile. Submitting (re)sets the
    profile to pending approval if it was never approved."""
    if not request.user.is_operator:
        messages.error(request, "Only operator accounts can set up a profile.")
        return redirect("home")

    operator = getattr(request.user, "operator", None)

    if request.method == "POST":
        form = OperatorProfileForm(request.POST, instance=operator)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = request.user
            obj.save()
            messages.success(
                request,
                "Profile saved. Our team will review and approve your account shortly.",
            )
            return redirect("operator_dashboard")
    else:
        form = OperatorProfileForm(instance=operator)

    return render(
        request,
        "operators/onboarding.html",
        {"form": form, "operator": operator},
    )


@operator_required
def dashboard(request):
    """Operator home — shows approval status and (once approved) the
    management sections built out in Phases 3–5."""
    return render(
        request,
        "operators/dashboard.html",
        {"operator": request.user.operator},
    )


@operator_required
def earnings(request):
    """Operator's money view: gross sales, commission deducted by the
    platform, and net payout."""
    op = request.user.operator
    commissions = op.commissions.select_related("booking", "booking__trip__route").order_by(
        "-created_at"
    )
    totals = commissions.aggregate(
        gross=Sum("gross_amount"),
        commission=Sum("commission_amount"),
        payout=Sum("payout_amount"),
        bookings=Count("id"),
    )
    return render(
        request,
        "operators/earnings.html",
        {"operator": op, "commissions": commissions, "totals": totals},
    )


def _is_platform_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


@user_passes_test(_is_platform_admin)
def platform_revenue(request):
    """Kept for backwards-compatible links — the revenue report now lives in
    the admin panel at /manage/revenue/."""
    return redirect("admin_revenue")
