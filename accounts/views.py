from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from bookings.cities import INDIAN_CITIES, PRIORITY_CITIES

from .forms import SignupForm


def home(request):
    """Landing page with the searchable From/To route dropdowns."""
    return render(
        request,
        "home.html",
        {"cities": INDIAN_CITIES, "priority_cities": PRIORITY_CITIES},
    )


def signup(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            if user.is_operator:
                messages.success(
                    request,
                    "Welcome! Next, set up your operator profile to start listing buses.",
                )
                return redirect("operator_onboarding")
            messages.success(request, "Account created. Happy travels!")
            return redirect("home")
    else:
        form = SignupForm()
    return render(request, "accounts/signup.html", {"form": form})


class RoleAwareLoginView(LoginView):
    """Standard login, but operators land on their dashboard by default."""

    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        url = self.get_redirect_url()
        if url:
            return url
        if self.request.user.is_operator:
            return "/operator/"
        return "/"
