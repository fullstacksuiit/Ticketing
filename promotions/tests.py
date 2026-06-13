from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from operators.models import Operator

from .models import Banner


class BannerLiveQuerySetTests(TestCase):
    def test_live_excludes_inactive(self):
        Banner.objects.create(title="Off", is_active=False)
        live = Banner.objects.create(title="On", is_active=True)
        self.assertEqual(list(Banner.objects.live()), [live])

    def test_live_respects_schedule_window(self):
        now = timezone.now()
        Banner.objects.create(title="Future", starts_at=now + timedelta(days=1))
        Banner.objects.create(title="Past", ends_at=now - timedelta(days=1))
        current = Banner.objects.create(
            title="Now", starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
        )
        no_window = Banner.objects.create(title="Always")
        self.assertCountEqual(Banner.objects.live(), [current, no_window])

    def test_live_filters_by_placement(self):
        hero = Banner.objects.create(title="H", placement=Banner.Placement.HERO)
        Banner.objects.create(title="S", placement=Banner.Placement.STRIP)
        self.assertEqual(
            list(Banner.objects.live(Banner.Placement.HERO)), [hero]
        )

    def test_live_ordered_by_sort_order(self):
        b2 = Banner.objects.create(title="Second", sort_order=2)
        b1 = Banner.objects.create(title="First", sort_order=1)
        self.assertEqual(list(Banner.objects.live()), [b1, b2])

    def test_is_live_property(self):
        now = timezone.now()
        self.assertFalse(Banner(title="x", is_active=False).is_live)
        self.assertFalse(
            Banner(title="x", starts_at=now + timedelta(hours=1)).is_live
        )
        self.assertTrue(Banner(title="x").is_live)


class FeaturedOperatorTests(TestCase):
    def setUp(self):
        self.op_user = User.objects.create_user(
            "acme", password="x", role=User.Role.OPERATOR
        )

    def _make(self, name, **kw):
        user = User.objects.create_user(name, password="x")
        return Operator.objects.create(
            user=user, company_name=name, contact_person="A",
            contact_phone="1", contact_email=f"{name}@t.test", **kw,
        )

    def test_featured_only_approved_and_flagged(self):
        approved = self._make(
            "Featured", is_featured=True, status=Operator.Status.APPROVED
        )
        self._make("Pending", is_featured=True, status=Operator.Status.PENDING)
        self._make("Plain", is_featured=False, status=Operator.Status.APPROVED)
        self.assertEqual(list(Operator.featured()), [approved])

    def test_featured_ordered_by_featured_order(self):
        b = self._make("B", is_featured=True,
                       status=Operator.Status.APPROVED, featured_order=2)
        a = self._make("A", is_featured=True,
                       status=Operator.Status.APPROVED, featured_order=1)
        self.assertEqual(list(Operator.featured()), [a, b])


class HomePagePromotionTests(TestCase):
    def test_live_banner_and_featured_operator_render(self):
        Banner.objects.create(
            title="Monsoon Sale", placement=Banner.Placement.STRIP
        )
        user = User.objects.create_user("acme", password="x")
        Operator.objects.create(
            user=user, company_name="Acme Travels", contact_person="A",
            contact_phone="1", contact_email="a@t.test",
            is_featured=True, status=Operator.Status.APPROVED,
        )
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Monsoon Sale")
        self.assertContains(resp, "Featured operators")
        self.assertContains(resp, "Acme Travels")

    def test_inactive_banner_hidden(self):
        Banner.objects.create(title="Hidden Promo", is_active=False)
        resp = self.client.get(reverse("home"))
        self.assertNotContains(resp, "Hidden Promo")


class AdminPanelPromotionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("boss", password="x", is_staff=True)
        self.client.force_login(self.staff)

    def test_banner_registered_in_panel_list(self):
        resp = self.client.get(
            reverse("admin_model_list", args=["promotions", "banner"])
        )
        self.assertEqual(resp.status_code, 200)

    def test_create_banner_through_panel(self):
        url = reverse("admin_model_add", args=["promotions", "banner"])
        resp = self.client.post(url, {
            "title": "Festive Offer",
            "subtitle": "",
            "image_url": "",
            "link_url": "/search/",
            "cta_label": "Book now",
            "placement": Banner.Placement.STRIP,
            "is_active": "on",
            "sort_order": "0",
            "starts_at": "",
            "ends_at": "",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Banner.objects.filter(title="Festive Offer").exists())

    def test_feature_action_flags_operator(self):
        user = User.objects.create_user("acme", password="x")
        op = Operator.objects.create(
            user=user, company_name="Acme", contact_person="A",
            contact_phone="1", contact_email="a@t.test",
            status=Operator.Status.APPROVED,
        )
        url = reverse("admin_model_action", args=["operators", "operator"])
        self.client.post(url, {"action": "feature", "_selected": [op.pk]})
        op.refresh_from_db()
        self.assertTrue(op.is_featured)
