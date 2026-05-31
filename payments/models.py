from django.db import models

from bookings.models import Booking
from operators.models import Operator


class Payment(models.Model):
    """Payment for a booking. Today it's a dummy gateway that always succeeds;
    the `method`/`gateway_ref` fields are here so Razorpay slots in later
    without a schema change."""

    class Method(models.TextChoices):
        DUMMY = "dummy", "Dummy (test)"
        RAZORPAY = "razorpay", "Razorpay"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, related_name="payment"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=12, choices=Method.choices, default=Method.DUMMY)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    gateway_ref = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.booking.pnr} · {self.amount} · {self.get_status_display()}"


class Commission(models.Model):
    """The money split for one booking, recorded when it's confirmed. Rate and
    amounts are snapshotted here so changing an operator's rate later doesn't
    rewrite history. payout = gross − commission; platform keeps commission."""

    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, related_name="commission"
    )
    operator = models.ForeignKey(
        Operator, on_delete=models.CASCADE, related_name="commissions"
    )
    gross_amount = models.DecimalField(max_digits=10, decimal_places=2)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2)
    payout_amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.booking.pnr} · platform ₹{self.commission_amount} / operator ₹{self.payout_amount}"
