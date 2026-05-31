from django.urls import path

from . import views

urlpatterns = [
    path("routes/", views.route_list, name="route_list"),
    path("routes/add/", views.route_add, name="route_add"),
    path("routes/<int:route_id>/", views.route_detail, name="route_detail"),
    path("routes/<int:route_id>/edit/", views.route_edit, name="route_edit"),
    path("stops/<int:stop_id>/delete/", views.stop_delete, name="stop_delete"),
    path("trips/", views.trip_list, name="trip_list"),
    path("trips/add/", views.trip_add, name="trip_add"),
    path("trips/<int:trip_id>/fares/", views.trip_fares, name="trip_fares"),
    path("schedules/", views.schedule_list, name="schedule_list"),
    path("schedules/add/", views.schedule_add, name="schedule_add"),
    path("schedules/<int:schedule_id>/edit/", views.schedule_edit, name="schedule_edit"),
    path("schedules/<int:schedule_id>/toggle/", views.schedule_toggle, name="schedule_toggle"),
]
