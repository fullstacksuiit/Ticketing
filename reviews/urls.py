from django.urls import path

from . import views

urlpatterns = [
    path("review/<str:pnr>/", views.review_create, name="review_create"),
]
