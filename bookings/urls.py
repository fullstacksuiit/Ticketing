from django.urls import path

from . import views

urlpatterns = [
    path("search/", views.search, name="search"),
    path("trip/<int:trip_id>/seats/", views.trip_seats, name="trip_seats"),
    path("trip/<int:trip_id>/book/", views.trip_book, name="trip_book"),
    path("ticket/<str:pnr>/", views.ticket, name="ticket"),
    path("ticket/<str:pnr>/download/", views.ticket_pdf, name="ticket_pdf"),
    path("find-ticket/", views.find_booking, name="find_booking"),
    path("my-bookings/", views.my_bookings, name="my_bookings"),
]
