from django import forms

from .models import Bus

INPUT = "input"


class BusForm(forms.ModelForm):
    class Meta:
        model = Bus
        fields = [
            "name",
            "registration_number",
            "is_ac",
            "is_sleeper",
            "has_upper_deck",
            "wifi",
            "charging_point",
            "water_bottle",
            "blanket",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs["class"] = INPUT
        self.fields["registration_number"].widget.attrs["class"] = INPUT
