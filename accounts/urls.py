from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("signup/", views.signup, name="signup"),
    path("login/", views.RoleAwareLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
]
