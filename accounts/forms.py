from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User

# Shared input styling — see the `.input` component class in base.html
INPUT_CLASS = "input"


class SignupForm(UserCreationForm):
    """Registration form. The user picks whether they are signing up as a
    passenger (to book tickets) or as a bus operator (to sell seats)."""

    email = forms.EmailField(required=True)
    phone = forms.CharField(max_length=15, required=False)
    role = forms.ChoiceField(
        choices=[
            (User.Role.PASSENGER, "I want to book tickets (Passenger)"),
            (User.Role.OPERATOR, "I run buses (Operator)"),
        ],
        widget=forms.RadioSelect,
        initial=User.Role.PASSENGER,
    )

    class Meta:
        model = User
        fields = ["username", "email", "phone", "role", "password1", "password2"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "role":
                continue
            field.widget.attrs.setdefault("class", INPUT_CLASS)
