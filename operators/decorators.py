from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def operator_required(view):
    """Allow only logged-in operator users. Operators without a profile yet
    are sent to onboarding."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        if not request.user.is_operator:
            messages.error(request, "That area is for bus operators only.")
            return redirect("home")
        if not hasattr(request.user, "operator"):
            return redirect("operator_onboarding")
        return view(request, *args, **kwargs)

    return wrapper
