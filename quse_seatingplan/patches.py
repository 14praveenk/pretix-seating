from collections import defaultdict
from typing import Dict, Set

from pretix.base.models import SeatCategoryMapping
from pretix.base.services import orders as order_services


def _seat_product_sets(event) -> Dict[str, object]:
    """Return lookup maps of product IDs that require seats for the event."""
    general_products: Set[int] = set()
    subevent_products: Dict[int, Set[int]] = defaultdict(set)
    qs = SeatCategoryMapping.objects.filter(event=event).values_list('product_id', 'subevent_id')
    for product_id, subevent_id in qs:
        if subevent_id is None:
            general_products.add(product_id)
        else:
            subevent_products[subevent_id].add(product_id)
    return {
        'general': general_products,
        'subevents': subevent_products,
    }


def _position_requires_seat(product_id: int, subevent_id: int, lookup: Dict[str, object]) -> bool:
    subevent_products: Dict[int, Set[int]] = lookup['subevents']
    if subevent_id is not None and subevent_products.get(subevent_id):
        if product_id in subevent_products[subevent_id]:
            return True
    return product_id in lookup['general']


_original_check_positions = order_services._check_positions
_patch_applied = getattr(order_services, '_quse_seatingplan_check_patch', False)


def _patched_check_positions(event, now_dt, time_machine_now_dt, positions, sales_channel, address=None, customer=None):
    if event.settings.get('quse_seatingplan_checkout_enabled', as_type=bool):
        lookup = _seat_product_sets(event)
        if lookup['general'] or lookup['subevents']:
            for position in positions:
                if _position_requires_seat(position.item_id, position.subevent_id, lookup):
                    position.requires_seat = True
    return _original_check_positions(event, now_dt, time_machine_now_dt, positions, sales_channel, address=address, customer=customer)


if not _patch_applied:
    order_services._check_positions = _patched_check_positions
    order_services._quse_seatingplan_check_patch = True
