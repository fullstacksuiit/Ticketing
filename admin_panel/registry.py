"""A small model registry that drives the custom admin panel.

Each registered model gets one `ModelConfig` describing how it lists, filters,
searches, forms, and what bulk actions it supports — the same knobs Django's
own ModelAdmin exposes. The generic views in views.py read these configs, so
adding a model to the branded panel is one register() call, not a new screen.

The configs here are ported 1:1 from the per-app admin.py files so the panel
reaches parity with Django's built-in /admin/.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, List, Optional

from django.db import models as dj_models
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings.models import BookedSeat, Booking
from buses.models import Bus, BusPhoto, Seat
from operators.models import Operator
from payments.models import Commission, Payment, Refund
from promotions.models import Banner
from reviews.models import Review
from routes.models import Route, Schedule, SegmentFare, Stop, Trip


# --------------------------------------------------------------------------- #
# Config primitives
# --------------------------------------------------------------------------- #
@dataclass
class Inline:
    """A child model edited inside its parent's form (Django inline admin)."""

    model: type
    fields: List[str]
    fk_name: Optional[str] = None
    extra: int = 1

    @property
    def prefix(self):
        return self.model._meta.model_name

    @property
    def title(self):
        return self.model._meta.verbose_name_plural.title()


@dataclass
class Action:
    """A bulk action runnable over selected rows from the list page."""

    name: str
    label: str
    fn: Callable  # fn(queryset) -> str (a user-facing result message)
    style: str = "btn-outline"  # css class for the button


@dataclass
class ModelConfig:
    model: type
    list_display: List[str]
    list_filter: List[str] = field(default_factory=list)
    search_fields: List[str] = field(default_factory=list)
    form_fields: Optional[List[str]] = None  # None -> all editable fields
    ordering: Optional[List[str]] = None
    inlines: List[Inline] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)
    can_add: bool = True
    can_delete: bool = True
    # Optional fn(obj) -> [{"label", "url"}] of links to this record's live
    # front-end pages (e.g. a booking's public ticket). Shown on the edit page.
    object_links: Optional[Callable] = None
    # Optional read-only info panel shown above the form on the edit page:
    # `detail_template` is a template path; `detail_context` is fn(obj) -> dict
    # merged into its context (alongside `instance`).
    detail_template: Optional[str] = None
    detail_context: Optional[Callable] = None

    @property
    def app_label(self):
        return self.model._meta.app_label

    @property
    def model_name(self):
        return self.model._meta.model_name

    @property
    def key(self):
        return f"{self.app_label}/{self.model_name}"

    @property
    def verbose_name(self):
        return self.model._meta.verbose_name.title()

    @property
    def verbose_name_plural(self):
        return self.model._meta.verbose_name_plural.title()


_REGISTRY: "dict[tuple, ModelConfig]" = {}


def register(config: ModelConfig):
    _REGISTRY[(config.app_label, config.model_name)] = config
    return config


def get_config(app_label: str, model_name: str) -> Optional[ModelConfig]:
    return _REGISTRY.get((app_label, model_name))


# --------------------------------------------------------------------------- #
# Bulk actions
# --------------------------------------------------------------------------- #
def _approve_operators(qs):
    n = qs.update(status=Operator.Status.APPROVED, approved_at=timezone.now())
    return f"{n} operator(s) approved."


def _reject_operators(qs):
    n = qs.update(status=Operator.Status.REJECTED)
    return f"{n} operator(s) rejected."


def _suspend_operators(qs):
    n = qs.update(status=Operator.Status.SUSPENDED)
    return f"{n} operator(s) suspended."


def _feature_operators(qs):
    n = qs.update(is_featured=True)
    return f"{n} operator(s) featured on the homepage."


def _unfeature_operators(qs):
    n = qs.update(is_featured=False)
    return f"{n} operator(s) removed from featured."


def _activate_users(qs):
    n = qs.update(is_active=True)
    return f"{n} user(s) activated."


def _deactivate_users(qs):
    n = qs.update(is_active=False)
    return f"{n} user(s) deactivated."


def _generate_schedule_trips(qs):
    from routes.services import generate_trips_for_schedule

    until = timezone.localdate() + timedelta(days=30)
    total = sum(len(generate_trips_for_schedule(s, until)) for s in qs)
    return f"{total} new trip(s) generated for the next 30 days."


def _booking_detail_context(booking):
    """Refund/cancellation summary for a booking's admin edit page: the recorded
    Refund (if cancelled), a live what-if refund quote (if still cancellable),
    and whether the booking can still be moved to another departure."""
    from bookings.refunds import quote_refund
    from bookings.services import reschedule_eligibility

    can_reschedule, _ = reschedule_eligibility(booking)
    return {
        "refund": getattr(booking, "refund", None),
        "quote": quote_refund(booking),
        "can_reschedule": can_reschedule,
        "reschedule_url": reverse("reschedule", args=[booking.pnr]),
    }


def _booking_object_links(booking):
    """Header links on a booking's edit page: always the live ticket, plus a
    reschedule link while the booking is still eligible to move."""
    from bookings.services import reschedule_eligibility

    links = [{"label": "View live ticket", "url": reverse("ticket", args=[booking.pnr])}]
    if reschedule_eligibility(booking)[0]:
        links.append({"label": "Reschedule", "url": reverse("reschedule", args=[booking.pnr])})
    return links


def _cancel_bookings(qs):
    # Route through the customer cancellation service so admin cancels also free
    # seats, record a refund, and re-split commission — uniformly. force=True
    # overrides the time-window policy (admin can cancel even after departure).
    from bookings.services import CancellationError, cancel_booking

    n = 0
    for booking in qs.exclude(status=Booking.Status.CANCELLED):
        try:
            cancel_booking(booking, force=True)
            n += 1
        except CancellationError:
            continue
    return f"{n} booking(s) cancelled, seats freed and refunds recorded."


# --------------------------------------------------------------------------- #
# Registrations (ported from each app's admin.py)
# --------------------------------------------------------------------------- #
register(ModelConfig(
    model=Operator,
    list_display=["company_name", "status", "commission_rate",
                  "is_self_operated", "is_featured", "city", "created_at"],
    list_filter=["status", "is_self_operated", "is_featured"],
    search_fields=["company_name", "contact_person", "contact_email"],
    form_fields=["user", "company_name", "contact_person", "contact_phone",
                 "contact_email", "address", "city", "state", "description",
                 "commission_rate", "is_self_operated", "is_featured",
                 "featured_order", "status"],
    actions=[
        Action("approve", "Approve", _approve_operators, "btn-success"),
        Action("reject", "Reject", _reject_operators),
        Action("suspend", "Suspend", _suspend_operators),
        Action("feature", "Feature", _feature_operators, "btn-success"),
        Action("unfeature", "Unfeature", _unfeature_operators),
    ],
))

register(ModelConfig(
    model=User,
    list_display=["username", "email", "role", "phone", "is_staff", "is_active"],
    list_filter=["role", "is_staff", "is_superuser", "is_active"],
    search_fields=["username", "email", "first_name", "last_name"],
    form_fields=["username", "email", "first_name", "last_name", "role",
                 "phone", "is_active", "is_staff", "is_superuser"],
    actions=[
        Action("activate", "Activate", _activate_users, "btn-success"),
        Action("deactivate", "Deactivate", _deactivate_users),
    ],
))

register(ModelConfig(
    model=Bus,
    list_display=["name", "operator", "registration_number",
                  "bus_type_label", "total_seats"],
    list_filter=["operator", "is_ac", "is_sleeper"],
    search_fields=["name", "registration_number"],
    form_fields=["operator", "name", "registration_number", "is_ac",
                 "is_sleeper", "has_upper_deck", "wifi", "charging_point",
                 "water_bottle", "blanket"],
    inlines=[
        Inline(Seat, fields=[
            "seat_number", "deck", "row", "col", "row_span", "seat_type",
            "price_modifier", "is_window", "is_ladies", "is_reserved", "is_active",
        ], extra=0),
        Inline(BusPhoto, fields=["image", "caption", "sort_order"], extra=1),
    ],
    object_links=lambda b: [
        {"label": "Open seat editor", "url": reverse("seat_editor", args=[b.pk])},
        {"label": "Manage photos", "url": reverse("bus_photos", args=[b.pk])},
    ],
))

register(ModelConfig(
    model=Route,
    list_display=["__str__", "operator", "base_fare", "distance_km"],
    list_filter=["operator"],
    search_fields=["source_city", "destination_city"],
    form_fields=["operator", "source_city", "destination_city", "base_fare",
                 "via_cities", "distance_km"],
    inlines=[Inline(Stop, fields=["kind", "city", "name", "address", "time"])],
))

register(ModelConfig(
    model=Trip,
    list_display=["route", "departure", "arrival", "bus", "fare",
                  "status", "schedule"],
    list_filter=["status", "bus__operator"],
    search_fields=["route__source_city", "route__destination_city"],
    form_fields=["bus", "route", "departure", "arrival", "fare",
                 "status", "schedule"],
    inlines=[Inline(SegmentFare, fields=["from_city", "to_city", "fare"])],
    object_links=lambda t: [{
        "label": "View seat map",
        "url": reverse("trip_seats", args=[t.pk]),
    }],
))

register(ModelConfig(
    model=Schedule,
    list_display=["route", "bus", "departure_time", "cadence",
                  "start_date", "end_date", "is_active"],
    list_filter=["is_active", "recurrence", "bus__operator"],
    search_fields=["route__source_city", "route__destination_city"],
    form_fields=["bus", "route", "departure_time", "arrival_time",
                 "arrival_day_offset", "recurrence", "weekdays",
                 "start_date", "end_date", "is_active"],
    actions=[Action("generate", "Generate trips (30d)",
                    _generate_schedule_trips, "btn-success")],
))

register(ModelConfig(
    model=Booking,
    list_display=["pnr", "trip", "user", "total_amount", "status", "created_at"],
    list_filter=["status"],
    search_fields=["pnr", "contact_email", "contact_phone"],
    form_fields=["user", "trip", "from_city", "to_city", "boarding_point",
                 "dropping_point", "boarding_city", "dropping_city",
                 "contact_email", "contact_phone", "total_amount", "status"],
    inlines=[Inline(BookedSeat, fields=[
        "seat", "passenger_name", "passenger_age", "passenger_gender", "fare",
    ])],
    actions=[Action("cancel", "Cancel booking", _cancel_bookings, "btn-outline")],
    object_links=_booking_object_links,
    detail_template="admin_panel/_booking_summary.html",
    detail_context=_booking_detail_context,
))

register(ModelConfig(
    model=Review,
    list_display=["rating", "operator", "bus", "display_name", "booking",
                  "created_at"],
    list_filter=["rating", "operator"],
    search_fields=["booking__pnr", "comment", "author_name"],
    form_fields=["rating", "comment"],
    can_add=False,  # Reviews are created by passengers after travelling.
))

register(ModelConfig(
    model=Payment,
    list_display=["booking", "amount", "method", "status", "created_at"],
    list_filter=["method", "status"],
    search_fields=["booking__pnr", "gateway_ref"],
    form_fields=["booking", "amount", "method", "status", "gateway_ref"],
))

register(ModelConfig(
    model=Commission,
    list_display=["booking", "operator", "gross_amount", "commission_rate",
                  "commission_amount", "payout_amount", "created_at"],
    list_filter=["operator"],
    search_fields=["booking__pnr"],
    form_fields=["booking", "operator", "gross_amount", "commission_rate",
                 "commission_amount", "payout_amount"],
))

register(ModelConfig(
    model=Refund,
    list_display=["booking", "amount", "percent", "status", "created_at"],
    list_filter=["status"],
    search_fields=["booking__pnr", "gateway_ref"],
    form_fields=["booking", "amount", "percent", "policy_note", "status",
                 "gateway_ref"],
))

register(ModelConfig(
    model=Banner,
    list_display=["title", "placement", "is_active", "sort_order",
                  "starts_at", "ends_at", "created_at"],
    list_filter=["placement", "is_active"],
    search_fields=["title", "subtitle"],
    form_fields=["title", "subtitle", "image_url", "link_url", "cta_label",
                 "placement", "is_active", "sort_order", "starts_at", "ends_at"],
))


# --------------------------------------------------------------------------- #
# Navigation — grouped for the sidebar/sub-nav. Each item references a
# registered config by (app_label, model_name).
# --------------------------------------------------------------------------- #
NAV_GROUPS = [
    ("Marketplace", [("operators", "operator"), ("accounts", "user")]),
    ("Inventory", [("buses", "bus"), ("routes", "route"),
                   ("routes", "trip"), ("routes", "schedule")]),
    ("Sales", [("bookings", "booking"), ("payments", "payment"),
               ("payments", "commission"), ("payments", "refund")]),
    ("Feedback", [("reviews", "review")]),
    ("Promotions", [("promotions", "banner")]),
]


def nav_groups():
    """Build template-ready nav data from NAV_GROUPS + the registry."""
    groups = []
    for title, keys in NAV_GROUPS:
        items = []
        for app_label, model_name in keys:
            cfg = get_config(app_label, model_name)
            if cfg:
                items.append({
                    "label": cfg.verbose_name_plural,
                    "app_label": app_label,
                    "model_name": model_name,
                })
        groups.append({"title": title, "items": items})
    return groups
