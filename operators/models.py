from decimal import Decimal

from django.conf import settings
from django.db import models


class Operator(models.Model):
    """A bus operator (company) on the marketplace. Each operator user has
    exactly one Operator profile. The platform earns `commission_rate` percent
    on every booking for this operator's buses — except the owner's own buses,
    which are marked self-operated and keep 100%."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SUSPENDED = "suspended", "Suspended"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="operator",
    )

    # Company profile
    company_name = models.CharField(max_length=120)
    contact_person = models.CharField(max_length=120)
    contact_phone = models.CharField(max_length=15)
    contact_email = models.EmailField()
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True, help_text="Shown to passengers.")

    # Marketplace controls (owner/admin sets these)
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("10.00"),
        help_text="Platform commission percent on this operator's bookings.",
    )
    is_self_operated = models.BooleanField(
        default=False,
        help_text="The platform owner's own buses — keep 100%, no commission.",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )

    # Promotional control (owner/admin) — surface this operator on the homepage.
    is_featured = models.BooleanField(
        default=False,
        help_text="Promote this operator on the homepage.",
    )
    featured_order = models.PositiveIntegerField(
        default=0,
        help_text="Lower numbers show first among featured operators.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["company_name"]

    def __str__(self):
        return self.company_name

    @classmethod
    def featured(cls):
        """Approved operators the owner has promoted, in display order."""
        return cls.objects.filter(
            is_featured=True, status=cls.Status.APPROVED
        ).order_by("featured_order", "company_name")

    @property
    def is_approved(self):
        return self.status == self.Status.APPROVED

    @property
    def effective_commission_rate(self):
        """Self-operated buses pay no commission."""
        return Decimal("0.00") if self.is_self_operated else self.commission_rate

    def commission_on(self, amount):
        """Platform's cut on a booking of `amount` (a Decimal)."""
        return (amount * self.effective_commission_rate / Decimal("100")).quantize(
            Decimal("0.01")
        )

    @property
    def rating(self):
        """Average passenger rating and review count across all this operator's
        trips, computed lazily to keep operators independent of the reviews app."""
        from reviews.models import rating_for

        return rating_for(operator=self)
