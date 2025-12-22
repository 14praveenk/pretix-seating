from django.urls import path
from pretix.multidomain import event_url

from .views import (
    EmbeddedSeatingPlanView,
    SeatAssignmentView,
    SeatingPlanDataView,
    SeatingPlanSettingsView,
)

app_name = "quse_seatingplan"

urlpatterns = [
    path(
        "control/event/<str:organizer>/<str:event>/settings/quse-seatingplan/",
        SeatingPlanSettingsView.as_view(),
        name="settings",
    ),
]


def _namespaced(pattern, view, name):
    base = [event_url(pattern, view, name=name)]
    tail = pattern.lstrip("^")
    placeholder = event_url(r"(?P<cart_namespace>[_]{0})" + tail, view, name=name)
    widget = event_url(
        r"w/(?P<cart_namespace>[a-zA-Z0-9]{16})/" + tail, view, name=name
    )
    return base + [placeholder, widget]


event_patterns = []

for route in (
    (r"^quse-seatingplan/frame/$", EmbeddedSeatingPlanView.as_view(), "frame"),
    (
        r"^(?P<subevent>[0-9]+)/quse-seatingplan/frame/$",
        EmbeddedSeatingPlanView.as_view(),
        "frame",
    ),
    (r"^quse-seatingplan/api/data/$", SeatingPlanDataView.as_view(), "seat-data"),
    (
        r"^(?P<subevent>[0-9]+)/quse-seatingplan/api/data/$",
        SeatingPlanDataView.as_view(),
        "seat-data",
    ),
    (r"^quse-seatingplan/api/assign/$", SeatAssignmentView.as_view(), "seat-assign"),
    (
        r"^(?P<subevent>[0-9]+)/quse-seatingplan/api/assign/$",
        SeatAssignmentView.as_view(),
        "seat-assign",
    ),
):
    event_patterns.extend(_namespaced(*route))
