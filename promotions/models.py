from django.db import models
from django.utils import timezone


class BannerQuerySet(models.QuerySet):
    def live(self, placement=None, now=None):
        """Banners that should show to passengers right now: active and inside
        their optional schedule window. Ordered for display (sort_order, newest)."""
        now = now or timezone.now()
        qs = self.filter(is_active=True).filter(
            models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now),
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now),
        )
        if placement is not None:
            qs = qs.filter(placement=placement)
        return qs.order_by("sort_order", "-created_at")


class Banner(models.Model):
    """A promotional banner the owner/admin controls from the panel — a hero
    strip on the homepage. Scheduling (starts_at/ends_at) + the active toggle
    give full promotional control without code changes."""

    class Placement(models.TextChoices):
        CAROUSEL = "carousel", "Homepage — ad carousel (rotating, top)"
        HERO = "hero", "Homepage — hero (full width)"
        STRIP = "strip", "Homepage — promo strip (cards)"

    title = models.CharField(max_length=120)
    subtitle = models.CharField(max_length=200, blank=True)
    image_url = models.URLField(
        blank=True, help_text="Background/illustration image URL (optional)."
    )
    link_url = models.CharField(
        max_length=300, blank=True,
        help_text="Where the banner's button links to (e.g. /search/?from=Delhi).",
    )
    cta_label = models.CharField(
        max_length=40, blank=True, default="Explore",
        help_text="Text on the banner's button. Hidden if there's no link.",
    )
    placement = models.CharField(
        max_length=10, choices=Placement.choices, default=Placement.STRIP
    )

    is_active = models.BooleanField(
        default=True, help_text="Untick to hide without deleting."
    )
    sort_order = models.PositiveIntegerField(
        default=0, help_text="Lower numbers show first."
    )
    starts_at = models.DateTimeField(
        null=True, blank=True, help_text="Optional: don't show before this time."
    )
    ends_at = models.DateTimeField(
        null=True, blank=True, help_text="Optional: stop showing after this time."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = BannerQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "-created_at"]

    def __str__(self):
        return self.title

    @property
    def is_live(self):
        """Whether this banner would show right now (active + in window)."""
        now = timezone.now()
        if not self.is_active:
            return False
        if self.starts_at and self.starts_at > now:
            return False
        if self.ends_at and self.ends_at < now:
            return False
        return True
