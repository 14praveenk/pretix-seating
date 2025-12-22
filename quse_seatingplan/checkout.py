from django.contrib import messages
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from pretix.helpers.http import redirect_to_url
from pretix.presale.checkoutflow import TemplateFlowStep
from pretix.presale.views import CartMixin

from .utils import build_seatingframe_url


class SeatingPlanCheckoutStep(CartMixin, TemplateFlowStep):
    priority = 44
    identifier = 'quse_seatingplan'
    template_name = 'quse_seatingplan/checkout_seating.html'
    label = _('Choose seats')
    icon = 'chair'

    @cached_property
    def seat_product_ids(self):
        return set(
            self.event.seat_category_mappings.filter(subevent=None).values_list('product_id', flat=True)
        )

    def is_applicable(self, request):
        self.request = request
        if not request.event.settings.get('quse_seatingplan_checkout_enabled', as_type=bool):
            return False
        if not request.event.seating_plan:
            return False
        if not self.seat_product_ids:
            return False
        positions = self._positions()
        return any(pos.item_id in self.seat_product_ids for pos in positions)

    def is_completed(self, request, warn=False):
        self.request = request
        if not self.is_applicable(request):
            return True
        for pos in self._positions():
            if pos.item_id in self.seat_product_ids and not pos.seat:
                if warn:
                    messages.error(request, _('Please choose seats for every ticket before continuing.'))
                return False
        return True

    def get_context_data(self, **kwargs):
        kwargs.setdefault('iframe_url', self._iframe_url())
        ctx = super().get_context_data(**kwargs)
        ctx['cart'] = self.get_cart()
        return ctx

    def post(self, request):
        self.request = request
        if not self.is_completed(request, warn=True):
            return self.render()
        next_url = self.get_next_url(request)
        if next_url:
            return redirect_to_url(next_url)
        return self.render()

    def _iframe_url(self):
        cart_namespace = None
        if self.request.resolver_match and 'cart_namespace' in self.request.resolver_match.kwargs:
            cart_namespace = self.request.resolver_match.kwargs['cart_namespace']
        subevent = self._current_subevent()
        return build_seatingframe_url(
            event=self.request.event,
            subevent=subevent,
            cart_namespace=cart_namespace,
        )

    def _current_subevent(self):
        if not self.request.event.has_subevents:
            return None
        for pos in self._positions():
            if pos.subevent_id:
                return pos.subevent
        return None

    def _positions(self):
        if not hasattr(self, '_quse_seatingplan_positions'):
            self._quse_seatingplan_positions = list(self.positions)
        return self._quse_seatingplan_positions
