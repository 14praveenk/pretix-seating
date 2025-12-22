import json
import pytest
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory
from django.utils import timezone
from django_scopes import scope
from pretix.base.models import CartPosition, Event, Order, Organizer
from pretix.base.models.seating import SeatCategoryMapping, SeatingPlan
from pretix.base.services.orders import _perform_order
from types import SimpleNamespace

from quse_seatingplan import patches  # noqa: F401
from quse_seatingplan.checkout import SeatingPlanCheckoutStep
from quse_seatingplan.forms import SeatingPlanSettingsForm
from quse_seatingplan.utils import build_seatingframe_url
from quse_seatingplan.views import (
    EmbeddedSeatingPlanView,
    SeatAssignmentView,
    SeatingPlanDataView,
    SeatingPlanSettingsView,
)


def test_build_seatingframe_url_includes_optional_kwargs(monkeypatch):
    captured = {}

    def fake_reverse(event, urlname, kwargs=None):
        captured["kwargs"] = kwargs
        assert urlname == "plugins:quse_seatingplan:frame"
        return "/plugin/frame/"

    monkeypatch.setattr("quse_seatingplan.utils.eventreverse", fake_reverse)

    event = SimpleNamespace()
    subevent = SimpleNamespace(pk=7)
    url = build_seatingframe_url(
        event,
        subevent=subevent,
        cart_namespace="abcd1234",
        voucher_code="PROMO",
    )

    assert captured["kwargs"]["subevent"] == 7
    assert captured["kwargs"]["cart_namespace"] == "abcd1234"
    assert url == "/plugin/frame/?iframe=1&voucher=PROMO"


def test_build_seatingframe_url_without_optional_kwargs(monkeypatch):
    def fake_reverse(event, urlname, kwargs=None):
        assert kwargs == {}
        assert urlname == "plugins:quse_seatingplan:frame"
        return "/plugin/frame/"

    monkeypatch.setattr("quse_seatingplan.utils.eventreverse", fake_reverse)

    event = SimpleNamespace()
    url = build_seatingframe_url(event)

    assert url == "/plugin/frame/?iframe=1"


@pytest.mark.django_db
def test_settings_form_accepts_obj_kwarg():
    organizer = Organizer.objects.create(name="Org", slug="org")
    event = Event.objects.create(
        organizer=organizer,
        name="Event",
        slug="event",
        date_from=timezone.now(),
        currency="EUR",
    )

    with scope(organizer=organizer):
        form = SeatingPlanSettingsForm(obj=event)

    assert form.event == event


@pytest.mark.django_db
def test_settings_form_disables_default_seat_choice(monkeypatch):
    organizer = Organizer.objects.create(name="Org", slug="org")
    event = Event.objects.create(
        organizer=organizer,
        name="Event",
        slug="event",
        date_from=timezone.now(),
        currency="EUR",
    )

    plan = SeatingPlan(organizer=organizer, name="Seats")
    plan.layout_data = {
        "categories": [{"name": "Front", "color": "#ff0000"}],
        "zones": [],
    }
    plan.save()
    event.seating_plan = plan
    event.save(update_fields=["seating_plan"])
    event.settings.seating_choice = True

    monkeypatch.setattr(
        "quse_seatingplan.forms.seating_service.generate_seats", lambda *a, **k: None
    )

    with scope(organizer=organizer):
        form = SeatingPlanSettingsForm(
            event=event,
            data={"plan_name": "Seats", "category__Front": []},
        )
        assert form.is_valid(), form.errors
        form.save()

    assert event.settings.seating_choice is False


def test_embedded_view_sets_xframe(monkeypatch):
    response = SimpleNamespace()

    def fake_dispatch(self, request, *args, **kwargs):
        return response

    monkeypatch.setattr(
        "quse_seatingplan.views.SeatingPlanView.dispatch", fake_dispatch
    )

    view = EmbeddedSeatingPlanView()
    result = view.dispatch(object())

    assert result.xframe_options_exempt is True


def _session_with_cart(event, cart_id="cart123"):
    session = SessionStore()
    session.create()
    session["carts"] = {cart_id: {}}
    session[f"current_cart_event_{event.pk}"] = cart_id
    session.save()
    return session, cart_id


def _pretix_request(
    event, method="get", path="/", data=None, content_type="application/json"
):
    rf = RequestFactory()
    if method.lower() == "post":
        req = rf.post(path, data=data or {}, content_type=content_type)
    else:
        req = getattr(rf, method)(path)
    req.event = event
    req.organizer = event.organizer
    req.customer = None
    req.user = AnonymousUser()
    req.sales_channel = SimpleNamespace(identifier="web")
    req.resolver_match = SimpleNamespace(kwargs={})
    return req


@pytest.mark.django_db
def test_seating_data_view_returns_payload():
    organizer = Organizer.objects.create(name="Org", slug="org")
    event = Event.objects.create(
        organizer=organizer,
        name="Event",
        slug="event",
        date_from=timezone.now(),
        currency="EUR",
    )
    plan = SeatingPlan(organizer=organizer, name="Plan")
    plan.layout_data = {
        "layout": {
            "size": {"width": 400, "height": 300},
            "zones": [
                {
                    "name": "Main",
                    "position": {"x": 0, "y": 0},
                    "rows": [],
                    "areas": [
                        {
                            "shape": "rectangle",
                            "color": "#cccccc",
                            "border_color": "#333333",
                            "position": {"x": 10, "y": 15},
                            "rectangle": {"width": 120, "height": 40},
                            "text": {
                                "text": "Stage Block",
                                "color": "#222222",
                                "size": 22,
                                "position": {"x": 60, "y": 20},
                            },
                        },
                        {
                            "shape": "text",
                            "position": {"x": 10, "y": 15},
                            "text": {
                                "text": "Stage",
                                "color": "#111111",
                                "size": 18,
                                "position": {"x": 60, "y": 20},
                            },
                        },
                    ],
                },
            ],
        },
        "categories": [{"name": "Front", "color": "#ff0000"}],
    }
    plan.save()
    event.seating_plan = plan
    event.save(update_fields=["seating_plan"])
    event.settings.quse_seatingplan_checkout_enabled = True

    with scope(organizer=organizer):
        item = event.items.create(name="Seat", default_price=10, admission=True)
        SeatCategoryMapping.objects.create(
            event=event, subevent=None, layout_category="Front", product=item
        )
        event.seats.create(
            seat_guid="Front-A-1",
            zone_name="Front",
            row_name="A",
            seat_number="1",
            product=item,
            x=10,
            y=20,
        )
        CartPosition.objects.create(
            event=event,
            item=item,
            price=Decimal("10.00"),
            expires=timezone.now() + timedelta(hours=1),
            cart_id="cart123",
        )

    request = _pretix_request(event)
    request.session, _ = _session_with_cart(event)
    with scope(organizer=organizer):
        response = SeatingPlanDataView.as_view()(request)

    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["seats"][0]["status"] == "free"
    assert payload["cart_positions"][0]["needs_seat"] is True
    assert payload["shapes"]
    rectangle = next(
        shape for shape in payload["shapes"] if shape["type"] == "rectangle"
    )
    assert rectangle["width"] == 120
    assert rectangle["label"] == "Stage Block"
    assert rectangle["label_color"] == "#222222"
    assert rectangle["label_size"] == 22
    assert rectangle["label_x"] == pytest.approx(70)
    assert rectangle["label_y"] == pytest.approx(35)
    text_shape = next(shape for shape in payload["shapes"] if shape["type"] == "text")
    assert text_shape["text"] == "Stage"


@pytest.mark.django_db
def test_seat_assignment_view_assigns_and_clears():
    organizer = Organizer.objects.create(name="Org", slug="org")
    event = Event.objects.create(
        organizer=organizer,
        name="Event",
        slug="event",
        date_from=timezone.now(),
        currency="EUR",
    )
    plan = SeatingPlan(organizer=organizer, name="Plan")
    plan.layout_data = {
        "layout": {"size": {"width": 400, "height": 300}},
        "categories": [{"name": "Front", "color": "#ff0000"}],
    }
    plan.save()
    event.seating_plan = plan
    event.save(update_fields=["seating_plan"])
    event.settings.quse_seatingplan_checkout_enabled = True

    with scope(organizer=organizer):
        item = event.items.create(name="Seat", default_price=10, admission=True)
        SeatCategoryMapping.objects.create(
            event=event, subevent=None, layout_category="Front", product=item
        )
        seat = event.seats.create(
            seat_guid="Front-A-1",
            zone_name="Front",
            row_name="A",
            seat_number="1",
            product=item,
            x=10,
            y=20,
        )
        cart_pos = CartPosition.objects.create(
            event=event,
            item=item,
            price=Decimal("10.00"),
            expires=timezone.now() + timedelta(hours=1),
            cart_id="cartABC",
        )

    session, _ = _session_with_cart(event, "cartABC")

    def _post(payload):
        request = _pretix_request(
            event,
            method="post",
            path="/assign",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.session = session
        with scope(organizer=organizer):
            return SeatAssignmentView.as_view()(request)

    assign_response = _post({"cart_position": cart_pos.pk, "seat_guid": seat.seat_guid})
    assert assign_response.status_code == 200
    cart_pos.refresh_from_db()
    assert cart_pos.seat == seat

    clear_response = _post({"cart_position": cart_pos.pk, "seat_guid": None})
    assert clear_response.status_code == 200
    cart_pos.refresh_from_db()
    assert cart_pos.seat is None


@pytest.mark.django_db
def test_settings_view_plan_summary():
    organizer = Organizer.objects.create(name="Org", slug="org")
    event = Event.objects.create(
        organizer=organizer,
        name="Event",
        slug="event",
        date_from=timezone.now(),
        currency="EUR",
    )
    plan = SeatingPlan(organizer=organizer, name="Main plan")
    plan.layout_data = {
        "categories": [{"name": "VIP", "color": "#ff0000"}],
        "zones": [],
    }
    plan.save()
    event.seating_plan = plan
    event.save(update_fields=["seating_plan"])
    with scope(organizer=organizer):
        item = event.items.create(name="Seat", default_price=10, admission=True)
        event.seats.create(
            seat_guid="VIP-1", seat_number="1", zone_name="VIP", product=item
        )
        SeatCategoryMapping.objects.create(
            event=event, subevent=None, layout_category="VIP", product=item
        )

    request = RequestFactory().get("/")
    request.event = event
    request.organizer = organizer

    view = SeatingPlanSettingsView()
    view.request = request

    summary = view._plan_summary()
    assert summary["name"] == "Main plan"
    assert summary["seat_count"] == 1
    assert summary["categories"][0]["products"] == [item]
    assert "VIP" in summary["layout_json"]


@pytest.mark.django_db
def test_settings_form_supports_multiple_products(monkeypatch):
    organizer = Organizer.objects.create(name="Org", slug="org")
    event = Event.objects.create(
        organizer=organizer,
        name="Event",
        slug="event-multi",
        date_from=timezone.now(),
        currency="EUR",
    )
    plan = SeatingPlan(organizer=organizer, name="Plan")
    plan.layout_data = {
        "categories": [{"name": "Front", "color": "#00ff00"}],
        "zones": [],
    }
    plan.save()
    event.seating_plan = plan
    event.save(update_fields=["seating_plan"])
    captured = {}

    def fake_generate(event_arg, subevent, plan_arg, mapping):
        captured["mapping"] = mapping

    monkeypatch.setattr(
        "quse_seatingplan.forms.seating_service.generate_seats", fake_generate
    )

    with scope(organizer=organizer):
        item_a = event.items.create(name="Seat A", default_price=10, admission=True)
        item_b = event.items.create(name="Seat B", default_price=12, admission=True)

        data = {
            "plan_name": "Plan",
            "category__Front": [str(item_a.pk), str(item_b.pk)],
        }
        form = SeatingPlanSettingsForm(event=event, data=data)
        assert form.is_valid(), form.errors
        form.save()

    mappings = SeatCategoryMapping.objects.filter(
        event=event, layout_category="Front"
    ).values_list("product_id", flat=True)
    assert set(mappings) == {item_a.pk, item_b.pk}
    assert captured["mapping"] == {}


def _request_for_event(event):
    request = RequestFactory().get("/")
    request.event = event
    request.session = {}
    request.resolver_match = SimpleNamespace(kwargs={})
    return request


@pytest.mark.django_db
def test_checkout_step_not_applicable_without_matching_items():
    organizer = Organizer.objects.create(name="Org", slug="org")
    event = Event.objects.create(
        organizer=organizer,
        name="Event",
        slug="event",
        date_from=timezone.now(),
        currency="EUR",
    )
    event.settings.quse_seatingplan_checkout_enabled = True
    event.seating_plan = SeatingPlan(organizer=organizer, name="Plan", layout="{}")

    with scope(organizer=organizer):
        seat_item = event.items.create(name="Seat", default_price=10, admission=True)
        other_item = event.items.create(name="Other", default_price=5, admission=True)
        SeatCategoryMapping.objects.create(
            event=event, subevent=None, layout_category="A", product=seat_item
        )

    request = _request_for_event(event)
    positions = [
        SimpleNamespace(
            item_id=other_item.pk, seat=None, subevent=None, subevent_id=None
        )
    ]

    step = SeatingPlanCheckoutStep(event)
    step._quse_seatingplan_positions = positions

    assert step.is_applicable(request) is False


@pytest.mark.django_db
def test_checkout_step_requires_filled_seats():
    organizer = Organizer.objects.create(name="Org", slug="org")
    event = Event.objects.create(
        organizer=organizer,
        name="Event",
        slug="event2",
        date_from=timezone.now(),
        currency="EUR",
    )
    event.settings.quse_seatingplan_checkout_enabled = True
    event.seating_plan = SeatingPlan(organizer=organizer, name="Plan", layout="{}")

    with scope(organizer=organizer):
        seat_item = event.items.create(name="Seat", default_price=10, admission=True)
        SeatCategoryMapping.objects.create(
            event=event, subevent=None, layout_category="A", product=seat_item
        )

    request = _request_for_event(event)
    positions = [
        SimpleNamespace(
            item_id=seat_item.pk, seat=None, subevent=None, subevent_id=None
        )
    ]

    step = SeatingPlanCheckoutStep(event)
    step._quse_seatingplan_positions = positions

    assert step.is_applicable(request) is True
    assert step.is_completed(request, warn=False) is False

    positions[0].seat = SimpleNamespace(pk=1)
    assert step.is_completed(request, warn=False) is True


@pytest.mark.django_db
def test_perform_order_preserves_plugin_seats():
    organizer = Organizer.objects.create(name="Org", slug="org")
    event = Event.objects.create(
        organizer=organizer,
        name="Event",
        slug="event-order",
        date_from=timezone.now(),
        currency="EUR",
    )
    event.settings.quse_seatingplan_checkout_enabled = True
    event.settings.seating_choice = False

    with scope(organizer=organizer):
        item = event.items.create(
            name="Seat", default_price=Decimal("10.00"), admission=True
        )
        SeatCategoryMapping.objects.create(
            event=event, subevent=None, layout_category="Main", product=item
        )
        seat = event.seats.create(
            seat_guid="Main-1",
            zone_name="Main",
            row_name="A",
            seat_number="1",
            product=item,
        )
        cart_pos = CartPosition.objects.create(
            event=event,
            item=item,
            price=Decimal("10.00"),
            expires=timezone.now() + timedelta(hours=1),
            cart_id="cartXYZ",
            seat=seat,
        )

    payment = [
        {
            "id": "manual",
            "provider": "manual",
            "max_value": None,
            "min_value": None,
            "multi_use_supported": False,
            "info_data": {},
        }
    ]

    with scope(organizer=organizer):
        result = _perform_order(
            event,
            payment,
            [cart_pos.pk],
            "buyer@example.org",
            "en",
            None,
            {},
            "web",
        )
        order = Order.objects.get(pk=result["order_id"])
        position = order.positions.first()
        assert position.seat == seat
