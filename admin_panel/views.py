"""Generic, registry-driven views that give the admin panel full CRUD parity
with Django's built-in /admin/, rendered in the BusGo Tailwind system.

A handful of views (list / form / delete / action) read a model's ModelConfig
and work for every registered model, plus two curated screens (dashboard and
the platform revenue report) layered on top.
"""

from datetime import date, datetime

from django.contrib import messages
from django.core.exceptions import FieldDoesNotExist
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.deletion import ProtectedError
from django.forms import inlineformset_factory, modelform_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from operators.models import Operator
from payments.models import Commission

from .decorators import admin_required
from .registry import get_config, nav_groups

PER_PAGE = 25


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _config_or_404(app_label, model_name):
    config = get_config(app_label, model_name)
    if config is None:
        raise Http404("No such admin model.")
    return config


def admin_render(request, template, ctx=None):
    """render() with the panel chrome (nav + pending-approval badge) injected."""
    ctx = ctx or {}
    ctx.setdefault("nav_groups", nav_groups())
    ctx.setdefault(
        "pending_ops",
        Operator.objects.filter(status=Operator.Status.PENDING).count(),
    )
    return render(request, template, ctx)


def _resolve_field(model, path):
    """Walk a `bus__operator`-style path and return the final model field."""
    fld = None
    current = model
    for part in path.split("__"):
        fld = current._meta.get_field(part)
        if fld.is_relation and fld.related_model:
            current = fld.related_model
    return fld


def _header_label(model, name):
    if name == "__str__":
        return model._meta.verbose_name.title()
    try:
        fld = model._meta.get_field(name)
        return (fld.verbose_name or name).title()
    except FieldDoesNotExist:
        return name.replace("_", " ").title()


def _display_value(obj, name):
    if name == "__str__":
        return str(obj)
    display = getattr(obj, f"get_{name}_display", None)
    if callable(display):
        return display()
    val = getattr(obj, name, "")
    if callable(val):
        val = val()
    return val


def _cell(obj, name):
    """Render one list-table cell to {type, value} for the template."""
    val = _display_value(obj, name)
    if isinstance(val, bool):
        return {"type": "bool", "value": val}
    if val is None or val == "":
        return {"type": "text", "value": "—"}
    if isinstance(val, datetime):
        local = timezone.localtime(val) if timezone.is_aware(val) else val
        return {"type": "text", "value": local.strftime("%d %b %Y, %H:%M")}
    if isinstance(val, date):
        return {"type": "text", "value": val.strftime("%d %b %Y")}
    return {"type": "text", "value": str(val)}


def _style_form(form):
    """Tag widgets with the project's Tailwind classes so generated forms look
    native. Checkboxes/radios keep their own styling."""
    for bound in form:
        widget = bound.field.widget
        input_type = getattr(widget, "input_type", None)
        if input_type in ("checkbox", "radio"):
            widget.attrs.setdefault("class", "h-4 w-4 rounded border-slate-300 text-brand focus:ring-brand/30")
        else:
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = (existing + " input").strip()
    return form


def _build_form_class(config):
    return modelform_factory(config.model, fields=config.form_fields)


def _build_formsets(config, instance, data=None, files=None):
    """Instantiate one inline formset per configured inline."""
    formsets = []
    for inline in config.inlines:
        FormSet = inlineformset_factory(
            config.model,
            inline.model,
            fields=inline.fields,
            fk_name=inline.fk_name,
            extra=inline.extra,
            can_delete=True,
        )
        fs = FormSet(data, files, instance=instance, prefix=inline.prefix)
        for sub in fs.forms:
            _style_form(sub)
        formsets.append({"inline": inline, "formset": fs})
    return formsets


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@admin_required
def dashboard(request):
    """Owner's home: headline KPIs, the operator-approval queue, and recent
    bookings — the everyday things an admin lands here to do."""
    today = timezone.localdate()

    confirmed = Booking.objects.filter(status=Booking.Status.CONFIRMED)
    sales = confirmed.aggregate(gross=Sum("total_amount"), n=Count("id"))
    commission = Commission.objects.aggregate(
        platform=Sum("commission_amount"), payout=Sum("payout_amount")
    )
    op_counts = {
        row["status"]: row["n"]
        for row in Operator.objects.values("status").annotate(n=Count("id"))
    }

    pending = (
        Operator.objects.filter(status=Operator.Status.PENDING)
        .select_related("user")
        .order_by("created_at")
    )
    recent_bookings = (
        Booking.objects.select_related("trip__route", "trip__bus__operator")
        .order_by("-created_at")[:8]
    )

    kpis = {
        "gross": sales["gross"] or 0,
        "commission": commission["platform"] or 0,
        "payout": commission["payout"] or 0,
        "bookings": sales["n"] or 0,
        "today_bookings": confirmed.filter(created_at__date=today).count(),
        "operators_total": sum(op_counts.values()),
        "operators_approved": op_counts.get(Operator.Status.APPROVED, 0),
        "operators_pending": op_counts.get(Operator.Status.PENDING, 0),
        "users": User.objects.count(),
    }

    return admin_render(request, "admin_panel/dashboard.html", {
        "kpis": kpis,
        "pending": pending,
        "recent_bookings": recent_bookings,
    })


# --------------------------------------------------------------------------- #
# Platform revenue (rehomed from operators.views.platform_revenue)
# --------------------------------------------------------------------------- #
@admin_required
def revenue(request):
    totals = Commission.objects.aggregate(
        gross=Sum("gross_amount"),
        commission=Sum("commission_amount"),
        payout=Sum("payout_amount"),
        bookings=Count("id"),
    )
    by_operator = (
        Operator.objects.annotate(
            gross=Sum("commissions__gross_amount"),
            commission=Sum("commissions__commission_amount"),
            payout=Sum("commissions__payout_amount"),
            bookings=Count("commissions"),
        )
        .filter(bookings__gt=0)
        .order_by("-commission")
    )
    return admin_render(request, "admin_panel/revenue.html", {
        "totals": totals,
        "by_operator": by_operator,
    })


# --------------------------------------------------------------------------- #
# Generic list
# --------------------------------------------------------------------------- #
@admin_required
def model_list(request, app_label, model_name):
    config = _config_or_404(app_label, model_name)
    model = config.model

    qs = model._default_manager.all()
    if config.ordering:
        qs = qs.order_by(*config.ordering)
    elif not model._meta.ordering:
        # Guarantee stable pagination for models with no default ordering.
        qs = qs.order_by("-pk")

    # Search
    query = request.GET.get("q", "").strip()
    if query and config.search_fields:
        cond = Q()
        for sf in config.search_fields:
            cond |= Q(**{f"{sf}__icontains": query})
        qs = qs.filter(cond)

    # Filters
    filters = []
    for path in config.list_filter:
        fld = _resolve_field(model, path)
        if fld.is_relation and fld.related_model:
            options = [(o.pk, str(o)) for o in fld.related_model._default_manager.all()]
        elif getattr(fld, "choices", None):
            options = list(fld.choices)
        elif fld.get_internal_type() == "BooleanField":
            options = [("True", "Yes"), ("False", "No")]
        else:
            options = []
        current = request.GET.get(path, "")
        if current != "":
            value = current
            if fld.get_internal_type() == "BooleanField":
                value = current == "True"
            qs = qs.filter(**{path: value})
        filters.append({
            "path": path,
            "label": _header_label(model, path.split("__")[0]),
            "options": [{"value": str(v), "label": str(lbl)} for v, lbl in options],
            "current": current,
        })

    headers = [_header_label(model, name) for name in config.list_display]

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))
    rows = [
        {"pk": obj.pk, "cells": [_cell(obj, name) for name in config.list_display]}
        for obj in page
    ]

    # Preserve search/filter querystring across pagination links
    params = request.GET.copy()
    params.pop("page", None)
    querystring = params.urlencode()

    return admin_render(request, "admin_panel/list.html", {
        "config": config,
        "headers": headers,
        "rows": rows,
        "page": page,
        "filters": filters,
        "query": query,
        "querystring": querystring,
        "total": paginator.count,
    })


# --------------------------------------------------------------------------- #
# Generic add / edit
# --------------------------------------------------------------------------- #
@admin_required
def model_form(request, app_label, model_name, pk=None):
    config = _config_or_404(app_label, model_name)
    model = config.model

    if pk is None and not config.can_add:
        raise Http404("Adding is disabled for this model.")

    instance = get_object_or_404(model, pk=pk) if pk is not None else model()
    FormClass = _build_form_class(config)

    if request.method == "POST":
        form = FormClass(request.POST, request.FILES, instance=instance)
        formsets = _build_formsets(config, instance, request.POST, request.FILES)
        ok = form.is_valid()

        if ok:
            saved = False
            try:
                with transaction.atomic():
                    obj = form.save(commit=False)
                    # New users have no password field in the generic form —
                    # block login until an admin sets one (Django admin / reset).
                    if isinstance(obj, User) and obj._state.adding and not obj.password:
                        obj.set_unusable_password()
                    obj.save()
                    form.save_m2m()
                    # Rebuild formsets bound to the now-saved parent so the FK
                    # is populated before validation/saving.
                    formsets = _build_formsets(config, obj, request.POST, request.FILES)
                    if not all(fsd["formset"].is_valid() for fsd in formsets):
                        raise _InlineInvalid
                    for fsd in formsets:
                        fsd["formset"].save()
                    saved = True
            except _InlineInvalid:
                saved = False

            if saved:
                verb = "updated" if pk is not None else "created"
                messages.success(request, f"{config.verbose_name} {verb}.")
                return redirect("admin_model_list", app_label, model_name)
    else:
        form = FormClass(instance=instance)
        formsets = _build_formsets(config, instance)

    _style_form(form)

    object_links = []
    detail = {}
    if pk is not None:
        if config.object_links:
            object_links = config.object_links(instance)
        if config.detail_context:
            detail = config.detail_context(instance)

    ctx = {
        "config": config,
        "form": form,
        "formsets": formsets,
        "is_add": pk is None,
        "instance": instance,
        "object_links": object_links,
    }
    ctx.update(detail)
    return admin_render(request, "admin_panel/form.html", ctx)


class _InlineInvalid(Exception):
    """Internal sentinel to roll back the transaction when an inline fails."""


# --------------------------------------------------------------------------- #
# Generic delete
# --------------------------------------------------------------------------- #
@admin_required
def model_delete(request, app_label, model_name, pk):
    config = _config_or_404(app_label, model_name)
    if not config.can_delete:
        raise Http404("Deleting is disabled for this model.")

    obj = get_object_or_404(config.model, pk=pk)

    if request.method == "POST":
        label = str(obj)
        try:
            obj.delete()
        except ProtectedError:
            messages.error(
                request,
                f"Can't delete “{label}” — other records still reference it.",
            )
            return redirect("admin_model_edit", app_label, model_name, pk)
        messages.success(request, f"{config.verbose_name} “{label}” deleted.")
        return redirect("admin_model_list", app_label, model_name)

    return admin_render(request, "admin_panel/confirm_delete.html", {
        "config": config,
        "object": obj,
    })


# --------------------------------------------------------------------------- #
# Generic bulk action
# --------------------------------------------------------------------------- #
@admin_required
def model_action(request, app_label, model_name):
    config = _config_or_404(app_label, model_name)
    list_url = reverse("admin_model_list", args=[app_label, model_name])

    if request.method != "POST":
        return redirect(list_url)

    name = request.POST.get("action", "")
    pks = request.POST.getlist("_selected")
    action = next((a for a in config.actions if a.name == name), None)

    if action is None:
        messages.error(request, "Unknown action.")
    elif not pks:
        messages.error(request, "No rows selected.")
    else:
        qs = config.model._default_manager.filter(pk__in=pks)
        messages.success(request, action.fn(qs))

    # Bounce back to the list, preserving where the action was triggered from.
    return redirect(request.POST.get("next") or list_url)
