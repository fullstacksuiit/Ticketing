from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user model. One account can be a passenger, a bus operator,
    or staff/admin. The role decides which dashboard and permissions apply."""

    class Role(models.TextChoices):
        PASSENGER = "passenger", "Passenger"
        OPERATOR = "operator", "Bus Operator"
        ADMIN = "admin", "Admin"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.PASSENGER,
    )
    phone = models.CharField(max_length=15, blank=True)

    @property
    def is_operator(self):
        return self.role == self.Role.OPERATOR

    @property
    def is_passenger(self):
        return self.role == self.Role.PASSENGER

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"
