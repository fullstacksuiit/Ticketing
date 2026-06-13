from django import forms

from .models import Review


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ["rating", "comment"]
        widgets = {
            "rating": forms.HiddenInput(),
            "comment": forms.Textarea(
                attrs={
                    "class": "input",
                    "rows": 4,
                    "placeholder": "How was the trip? Comfort, punctuality, staff…",
                }
            ),
        }

    def clean_rating(self):
        rating = self.cleaned_data.get("rating")
        if not rating or not (1 <= rating <= 5):
            raise forms.ValidationError("Please pick a rating from 1 to 5 stars.")
        return rating
