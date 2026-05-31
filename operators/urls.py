from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="operator_dashboard"),
    path("onboarding/", views.onboarding, name="operator_onboarding"),
    path("earnings/", views.earnings, name="operator_earnings"),
    path("revenue/", views.platform_revenue, name="platform_revenue"),
]
