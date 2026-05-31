from django import forms

from buses.models import Bus

from .models import Route, Schedule, Stop, Trip

INPUT = "input"


def _style(form):
    for f in form.fields.values():
        css = f.widget.attrs.get("class", "")
        if not isinstance(f.widget, (forms.CheckboxInput,)):
            f.widget.attrs["class"] = (css + " " + INPUT).strip()


class RouteForm(forms.ModelForm):
    class Meta:
        model = Route
        fields = [
            "source_city",
            "via_cities",
            "destination_city",
            "distance_km",
            "base_fare",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class StopForm(forms.ModelForm):
    class Meta:
        model = Stop
        fields = ["kind", "city", "name", "address", "time"]
        widgets = {"time": forms.TimeInput(attrs={"type": "time"})}

    def __init__(self, *args, route=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict the stop's city to this route's stages: source, via…, dest.
        if route is not None:
            choices = [(c, c) for c in route.stages]
            self.fields["city"] = forms.ChoiceField(
                choices=choices, label="City"
            )
        _style(self)


class TripForm(forms.ModelForm):
    class Meta:
        model = Trip
        fields = ["bus", "route", "departure", "arrival", "status"]
        widgets = {
            "departure": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "arrival": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, operator=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Operators can only schedule their own buses on their own routes
        if operator is not None:
            self.fields["bus"].queryset = operator.buses.all()
            self.fields["route"].queryset = operator.routes.all()
        self.fields["departure"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["arrival"].input_formats = ["%Y-%m-%dT%H:%M"]
        _style(self)

    def clean(self):
        cleaned = super().clean()
        dep, arr = cleaned.get("departure"), cleaned.get("arrival")
        if dep and arr and arr <= dep:
            self.add_error("arrival", "Arrival must be after departure.")
        return cleaned


class ScheduleForm(forms.ModelForm):
    """Create/edit a recurring schedule. Weekdays are picked as checkboxes and
    stored as a comma-separated string on the model."""

    weekdays = forms.MultipleChoiceField(
        choices=Schedule.WEEKDAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Used only when recurrence is 'Specific weekdays'.",
    )

    class Meta:
        model = Schedule
        fields = [
            "bus",
            "route",
            "departure_time",
            "arrival_time",
            "arrival_day_offset",
            "recurrence",
            "weekdays",
            "start_date",
            "end_date",
            "is_active",
        ]
        widgets = {
            "departure_time": forms.TimeInput(attrs={"type": "time"}),
            "arrival_time": forms.TimeInput(attrs={"type": "time"}),
            "arrival_day_offset": forms.Select(
                choices=[
                    (0, "Same day"),
                    (1, "Next day (overnight)"),
                    (2, "2 days later"),
                ],
            ),
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        labels = {"arrival_day_offset": "Arrival day"}

    def __init__(self, *args, operator=None, **kwargs):
        super().__init__(*args, **kwargs)
        if operator is not None:
            self.fields["bus"].queryset = operator.buses.all()
            self.fields["route"].queryset = operator.routes.all()
        self.fields["start_date"].input_formats = ["%Y-%m-%d"]
        self.fields["end_date"].input_formats = ["%Y-%m-%d"]
        # Pre-tick weekday boxes from the stored CSV when editing.
        if self.instance and self.instance.pk and self.instance.weekdays:
            self.fields["weekdays"].initial = [
                x for x in self.instance.weekdays.split(",") if x
            ]
        _style(self)

    def clean(self):
        cleaned = super().clean()
        dep = cleaned.get("departure_time")
        arr = cleaned.get("arrival_time")
        offset = cleaned.get("arrival_day_offset") or 0
        if dep and arr and offset == 0 and arr <= dep:
            self.add_error(
                "arrival_time",
                "Arrival must be after departure on the same day. For an "
                "overnight trip, set 'arrival day offset' to 1.",
            )

        recurrence = cleaned.get("recurrence")
        weekdays = cleaned.get("weekdays") or []
        if recurrence == Schedule.Recurrence.WEEKLY and not weekdays:
            self.add_error("weekdays", "Pick at least one weekday.")

        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date can't be before start date.")
        return cleaned

    def clean_weekdays(self):
        # Store the multiselect as a sorted comma-separated string.
        return ",".join(sorted(self.cleaned_data.get("weekdays", []), key=int))
