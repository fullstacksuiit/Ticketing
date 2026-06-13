from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="admin_dashboard"),
    path("revenue/", views.revenue, name="admin_revenue"),

    # Generic per-model CRUD. `m/<app>/<model>/...`
    path("m/<str:app_label>/<str:model_name>/",
         views.model_list, name="admin_model_list"),
    path("m/<str:app_label>/<str:model_name>/add/",
         views.model_form, name="admin_model_add"),
    path("m/<str:app_label>/<str:model_name>/action/",
         views.model_action, name="admin_model_action"),
    path("m/<str:app_label>/<str:model_name>/<int:pk>/edit/",
         views.model_form, name="admin_model_edit"),
    path("m/<str:app_label>/<str:model_name>/<int:pk>/delete/",
         views.model_delete, name="admin_model_delete"),
]
