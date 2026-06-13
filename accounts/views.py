from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from bookings.cities import INDIAN_CITIES, PRIORITY_CITIES
from operators.models import Operator
from promotions.models import Banner

from .forms import SignupForm


def home(request):
    """Landing page with the searchable From/To route dropdowns, plus any
    admin-controlled promotional banners and featured operators."""
    return render(
        request,
        "home.html",
        {
            "cities": INDIAN_CITIES,
            "priority_cities": PRIORITY_CITIES,
            "carousel_banners": Banner.objects.live(Banner.Placement.CAROUSEL),
            "hero_banners": Banner.objects.live(Banner.Placement.HERO),
            "strip_banners": Banner.objects.live(Banner.Placement.STRIP),
            "featured_operators": Operator.featured(),
        },
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
