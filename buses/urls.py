from django.urls import path

from . import views

urlpatterns = [
    path("buses/", views.bus_list, name="bus_list"),
    path("buses/add/", views.bus_add, name="bus_add"),
    path("buses/<int:bus_id>/seats/", views.seat_editor, name="seat_editor"),
]
