from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from operators.models import Operator


class AccessControlTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("boss", password="x", is_staff=True)
        self.passenger = User.objects.create_user("pat", password="x")

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.url)

    def test_non_staff_blocked(self):
        self.client.force_login(self.passenger)
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(resp.status_code, 302)  # bounced home

    def test_staff_allowed(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(resp.status_code, 200)


class OperatorWorkflowTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("boss", password="x", is_staff=True)
        self.client.force_login(self.staff)
        op_user = User.objects.create_user("acme", password="x", role=User.Role.OPERATOR)
        self.op = Operator.objects.create(
            user=op_user,
            company_name="Acme Travels",
            contact_person="A",
            contact_phone="123",
            contact_email="a@acme.test",
            status=Operator.Status.PENDING,
        )

    def test_list_renders(self):
        resp = self.client.get(reverse("admin_model_list", args=["operators", "operator"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Acme Travels")

    def test_approve_action_sets_status_and_timestamp(self):
        url = reverse("admin_model_action", args=["operators", "operator"])
        resp = self.client.post(url, {"action": "approve", "_selected": [self.op.pk]})
        self.assertEqual(resp.status_code, 302)
        self.op.refresh_from_db()
        self.assertEqual(self.op.status, Operator.Status.APPROVED)
        self.assertIsNotNone(self.op.approved_at)

    def test_edit_commission_persists(self):
        url = reverse("admin_model_edit", args=["operators", "operator", self.op.pk])
        data = {
            "user": self.op.user_id,
            "company_name": "Acme Travels",
            "contact_person": "A",
            "contact_phone": "123",
            "contact_email": "a@acme.test",
            "address": "",
            "city": "",
            "state": "",
            "description": "",
            "commission_rate": "15.50",
            "status": Operator.Status.APPROVED,
            "featured_order": "0",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.op.refresh_from_db()
        self.assertEqual(self.op.commission_rate, Decimal("15.50"))
        self.assertEqual(self.op.status, Operator.Status.APPROVED)

    def test_search_filters_results(self):
        url = reverse("admin_model_list", args=["operators", "operator"])
        resp = self.client.get(url, {"q": "nonexistent"})
        self.assertNotContains(resp, "Acme Travels")


class UserManagementTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("boss", password="x", is_staff=True)
        self.client.force_login(self.staff)

    def test_add_user_gets_unusable_password(self):
        url = reverse("admin_model_add", args=["accounts", "user"])
        data = {
            "username": "newbie",
            "email": "n@test.com",
            "first_name": "",
            "last_name": "",
            "role": User.Role.PASSENGER,
            "phone": "",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        created = User.objects.get(username="newbie")
        self.assertFalse(created.has_usable_password())

    def test_deactivate_action(self):
        target = User.objects.create_user("temp", password="x", is_active=True)
        url = reverse("admin_model_action", args=["accounts", "user"])
        self.client.post(url, {"action": "deactivate", "_selected": [target.pk]})
        target.refresh_from_db()
        self.assertFalse(target.is_active)


class BookingSummaryPanelTests(TestCase):
    def setUp(self):
        from buses.models import Bus
        from payments.models import Commission
        from routes.models import Route, Trip
        from bookings.models import Booking

        self.staff = User.objects.create_user("boss", password="x", is_staff=True)
        self.client.force_login(self.staff)

        op_user = User.objects.create_user("acme", password="x", role=User.Role.OPERATOR)
        self.op = Operator.objects.create(
            user=op_user, company_name="Acme", contact_person="A",
            contact_phone="1", contact_email="a@a.test",
            status=Operator.Status.APPROVED, commission_rate=Decimal("10.00"),
        )
        bus = Bus.objects.create(operator=self.op, name="B", registration_number="R1")
        route = Route.objects.create(
            operator=self.op, source_city="X", destination_city="Y",
            base_fare=Decimal("1000"),
        )
        now = timezone.now()
        self.trip = Trip.objects.create(
            bus=bus, route=route, departure=now + timedelta(days=3),
            arrival=now + timedelta(days=3, hours=5), fare=Decimal("1000"),
        )
        self.booking = Booking.objects.create(
            trip=self.trip, contact_email="c@c.test", contact_phone="9",
            total_amount=Decimal("1000"), status=Booking.Status.CONFIRMED,
        )
        Commission.objects.create(
            booking=self.booking, operator=self.op, gross_amount=Decimal("1000"),
            commission_rate=Decimal("10.00"), commission_amount=Decimal("100"),
            payout_amount=Decimal("900"),
        )

    def test_panel_shows_live_quote_for_confirmed(self):
        url = reverse("admin_model_edit", args=["bookings", "booking", self.booking.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cancellation &amp; refund")
        # 3 days out -> top slab 90% refund of 1000
        self.assertContains(resp, "Cancel &amp; refund ₹900.00")

    def test_inline_cancel_action_creates_refund(self):
        from payments.models import Refund
        from bookings.models import Booking

        url = reverse("admin_model_action", args=["bookings", "booking"])
        resp = self.client.post(url, {"action": "cancel", "_selected": [self.booking.pk]})
        self.assertEqual(resp.status_code, 302)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CANCELLED)
        self.assertIsNotNone(self.booking.cancelled_at)
        refund = Refund.objects.get(booking=self.booking)
        self.assertEqual(refund.amount, Decimal("900.00"))
        # commission re-split onto the ₹100 retained fee at 10%
        self.booking.commission.refresh_from_db()
        self.assertEqual(self.booking.commission.gross_amount, Decimal("100.00"))
        self.assertEqual(self.booking.commission.commission_amount, Decimal("10.00"))

    def test_panel_renders_refund_record_after_cancel(self):
        from bookings.services import cancel_booking
        cancel_booking(self.booking, force=True)
        url = reverse("admin_model_edit", args=["bookings", "booking", self.booking.pk])
        resp = self.client.get(url)
        self.assertContains(resp, "Refunded")
        self.assertContains(resp, "Cancelled")


class OperatorFeatureActionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("boss", password="x", is_staff=True)
        self.client.force_login(self.staff)
        op_user = User.objects.create_user("acme", password="x", role=User.Role.OPERATOR)
        self.op = Operator.objects.create(
            user=op_user, company_name="Acme", contact_person="A",
            contact_phone="1", contact_email="a@a.test",
            status=Operator.Status.APPROVED,
        )

    def test_feature_then_unfeature(self):
        url = reverse("admin_model_action", args=["operators", "operator"])
        self.client.post(url, {"action": "feature", "_selected": [self.op.pk]})
        self.op.refresh_from_db()
        self.assertTrue(self.op.is_featured)
        self.client.post(url, {"action": "unfeature", "_selected": [self.op.pk]})
        self.op.refresh_from_db()
        self.assertFalse(self.op.is_featured)


class BannerAdminTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("boss", password="x", is_staff=True)
        self.client.force_login(self.staff)

    def test_list_and_add_render(self):
        self.assertEqual(
            self.client.get(reverse("admin_model_list", args=["promotions", "banner"])).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("admin_model_add", args=["promotions", "banner"])).status_code, 200
        )

    def test_create_banner_persists(self):
        from promotions.models import Banner

        url = reverse("admin_model_add", args=["promotions", "banner"])
        data = {
            "title": "Monsoon Sale", "subtitle": "", "image_url": "", "link_url": "",
            "cta_label": "Explore", "placement": Banner.Placement.STRIP,
            "is_active": "on", "sort_order": "0", "starts_at": "", "ends_at": "",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Banner.objects.filter(title="Monsoon Sale").exists())


class BookingRescheduleLinkTests(TestCase):
    def setUp(self):
        from buses.models import Bus
        from routes.models import Route, Trip
        from bookings.models import Booking

        self.staff = User.objects.create_user("boss", password="x", is_staff=True)
        self.client.force_login(self.staff)
        op_user = User.objects.create_user("acme", password="x", role=User.Role.OPERATOR)
        op = Operator.objects.create(
            user=op_user, company_name="Acme", contact_person="A",
            contact_phone="1", contact_email="a@a.test",
            status=Operator.Status.APPROVED,
        )
        bus = Bus.objects.create(operator=op, name="B", registration_number="R1")
        route = Route.objects.create(
            operator=op, source_city="X", destination_city="Y", base_fare=Decimal("1000"),
        )
        now = timezone.now()
        # Far-future trip -> reschedule still open.
        trip = Trip.objects.create(
            bus=bus, route=route, departure=now + timedelta(days=3),
            arrival=now + timedelta(days=3, hours=5), fare=Decimal("1000"),
        )
        self.future = Booking.objects.create(
            trip=trip, contact_email="c@c.test", contact_phone="9",
            total_amount=Decimal("1000"), status=Booking.Status.CONFIRMED,
        )

    def test_eligible_booking_shows_reschedule_link(self):
        url = reverse("admin_model_edit", args=["bookings", "booking", self.future.pk])
        resp = self.client.get(url)
        self.assertContains(resp, reverse("reschedule", args=[self.future.pnr]))
        self.assertContains(resp, "Reschedule")


class RevenueRedirectTests(TestCase):
    def test_old_revenue_url_redirects(self):
        staff = User.objects.create_user("boss", password="x", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(reverse("platform_revenue"))
        self.assertRedirects(resp, reverse("admin_revenue"), fetch_redirect_response=False)
