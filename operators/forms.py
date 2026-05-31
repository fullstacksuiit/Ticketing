from django import forms

from .models import Operator

INPUT_CLASS = "input"


class OperatorProfileForm(forms.ModelForm):
    """Operator-editable company details. Commission, status and the
    self-operated flag are deliberately NOT here — only the owner/admin
    controls those."""

    class Meta:
        model = Operator
        fields = [
            "company_name",
            "contact_person",
            "contact_phone",
            "contact_email",
            "address",
            "city",
            "state",
            "description",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 2}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " " + INPUT_CLASS).strip()
