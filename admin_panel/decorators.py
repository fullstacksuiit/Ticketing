from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def admin_required(view):
    """Allow only platform staff/owner into the admin panel. Mirrors the
    `is_staff or is_superuser` gate the old platform_revenue view used. The
    `admin` *role* on a user is informational — actual access is by is_staff,
    so superusers and staff created via Django admin work without a role flip."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        if not (request.user.is_staff or request.user.is_superuser):
            messages.error(request, "That area is for platform administrators only.")
            return redirect("home")
        return view(request, *args, **kwargs)

    return wrapper
