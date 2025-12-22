import json

from django.db import transaction
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from pretix.base.models import CartPosition, Event, SeatCategoryMapping
from pretix.base.models.seating import Seat
from pretix.control.views.event import EventSettingsFormView, EventSettingsViewMixin
from pretix.presale.views import CartMixin, EventViewMixin, allow_cors_if_namespaced
from pretix.presale.views.cart import get_or_create_cart_id
from pretix.presale.views.event import SeatingPlanView

from .forms import SeatingPlanSettingsForm


class SeatingPlanSettingsView(EventSettingsViewMixin, EventSettingsFormView):
    model = Event
    form_class = SeatingPlanSettingsForm
    template_name = 'quse_seatingplan/settings.html'
    permission = 'can_change_settings'
    success_message = _('Seating plan configuration updated.')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.request.event
        return kwargs

    def get_success_url(self):
        return reverse('plugins:quse_seatingplan:settings', kwargs={
            'organizer': self.request.event.organizer.slug,
            'event': self.request.event.slug,
        })

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['plan_summary'] = self._plan_summary()
        return ctx

    def _plan_summary(self):
        plan = self.request.event.seating_plan
        if not plan:
            return None
        categories = [c.name for c in plan.get_categories()]
        seat_count = self.request.event.seats.filter(subevent=None).count()
        mapping_rows = SeatCategoryMapping.objects.filter(
            event=self.request.event,
            subevent=None,
        ).select_related('product')
        mappings = {}
        for mapping in mapping_rows:
            mappings.setdefault(mapping.layout_category, []).append(mapping.product)
        category_rows = [
            {
                'name': name,
                'products': mappings.get(name, []),
            }
            for name in categories
        ]
        return {
            'name': plan.name,
            'seat_count': seat_count,
            'categories': category_rows,
            'layout_json': json.dumps(plan.layout_data, indent=2),
        }


class EmbeddedSeatingPlanView(SeatingPlanView):
    """Drop X-Frame-Options so the plan can be embedded on the product page."""

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response.xframe_options_exempt = True
        return response


class SeatingPlanDataView(EventViewMixin, CartMixin, View):
    http_method_names = ['get']

    def get(self, request, *args, **kwargs):
        if not request.event.settings.get('quse_seatingplan_checkout_enabled', as_type=bool):
            return self._json_error(_('Seat selection is disabled for this event.'), status=404)
        try:
            subevent = self._get_subevent()
        except Http404:
            return self._json_error(_('Unknown subevent.'), status=404)
        owner = subevent or request.event
        if not owner.seating_plan:
            return self._json_error(_('No seating plan is configured for this event.'), status=404)
        payload = self._build_payload(owner.seating_plan, subevent)
        return JsonResponse(payload)

    def _get_subevent(self):
        if not self.request.event.has_subevents:
            return None
        subevent_id = self.kwargs.get('subevent')
        if not subevent_id:
            return None
        return self.request.event.subevents.filter(pk=subevent_id).first() or self._raise_not_found()

    def _raise_not_found(self):
        raise Http404()

    @staticmethod
    def _json_error(message, status=400):
        return JsonResponse({'error': message}, status=status)

    def _build_payload(self, plan, subevent):
        positions = self._cart_positions(subevent)
        seat_qs = self._annotated_seats(subevent)
        legend, product_colors = self._legend(plan, subevent)
        my_seat_ids = {pos.seat_id: pos.pk for pos in positions if pos.seat_id}
        seats = []
        bounds = self._empty_bounds()
        for seat in seat_qs:
            x = seat.x or 0
            y = seat.y or 0
            self._extend_bounds(bounds, x, y)
            status = self._seat_status(seat, my_seat_ids)
            seats.append({
                'guid': seat.seat_guid,
                'x': x,
                'y': y,
                'status': status,
                'product_id': seat.product_id,
                'label': str(seat),
                'color': product_colors.get(seat.product_id),
            })
        tickets = [
            {
                'id': pos.pk,
                'item_id': pos.item_id,
                'item_name': str(pos.item),
                'seat_guid': pos.seat.seat_guid if pos.seat else None,
                'seat_label': str(pos.seat) if pos.seat else None,
                'needs_seat': not pos.seat_id,
                'color': product_colors.get(pos.item_id),
            }
            for pos in positions
        ]
        shapes = self._shapes(plan, bounds)
        layout = plan.layout_data or {}
        layout_size = (layout.get('layout') or {}).get('size') or {}
        return {
            'meta': {
                'width': layout_size.get('width'),
                'height': layout_size.get('height'),
                'bounds': bounds,
                'needs_seats': sum(1 for pos in positions if not pos.seat_id),
            },
            'categories': legend,
            'cart_positions': tickets,
            'seats': seats,
            'shapes': shapes,
        }

    def _seat_status(self, seat, my_seat_ids):
        if seat.pk in my_seat_ids:
            return 'mine'
        if seat.blocked:
            return 'blocked'
        if seat.has_order or seat.has_cart or seat.has_voucher:
            return 'taken'
        return 'free'

    def _annotated_seats(self, subevent):
        qs = self.request.event.seats.filter(subevent=subevent)
        return Seat.annotated(qs, self.request.event.pk, subevent)

    def _cart_positions(self, subevent):
        target = subevent.pk if subevent else None
        product_ids = set(
            SeatCategoryMapping.objects.filter(
                event=self.request.event,
                subevent=subevent,
            ).values_list('product_id', flat=True)
        )
        return [
            pos for pos in self.positions
            if pos.item_id in product_ids and pos.subevent_id == target
        ]

    def _legend(self, plan, subevent):
        palette = {cat.get('name'): cat.get('color') for cat in (plan.layout_data or {}).get('categories', [])}
        legend = []
        product_colors = {}
        mappings = SeatCategoryMapping.objects.filter(event=self.request.event, subevent=subevent).select_related('product')
        for mapping in mappings:
            color = palette.get(mapping.layout_category)
            legend.append({
                'category': mapping.layout_category,
                'color': color,
                'product_id': mapping.product_id,
                'product': str(mapping.product) if mapping.product else None,
            })
            if mapping.product_id and mapping.product_id not in product_colors:
                product_colors[mapping.product_id] = color
        return legend, product_colors

    @staticmethod
    def _empty_bounds():
        return {'min_x': None, 'max_x': None, 'min_y': None, 'max_y': None}

    @staticmethod
    def _extend_bounds(bounds, x, y):
        if x is None or y is None:
            return
        if bounds['min_x'] is None or x < bounds['min_x']:
            bounds['min_x'] = x
        if bounds['max_x'] is None or x > bounds['max_x']:
            bounds['max_x'] = x
        if bounds['min_y'] is None or y < bounds['min_y']:
            bounds['min_y'] = y
        if bounds['max_y'] is None or y > bounds['max_y']:
            bounds['max_y'] = y

    def _shapes(self, plan, bounds):
        layout_data = plan.layout_data or {}
        layout = layout_data.get('layout') or layout_data
        shapes = []
        for zone in (layout.get('zones') or []):
            zone_pos = zone.get('position') or {}
            zone_x = zone_pos.get('x', 0)
            zone_y = zone_pos.get('y', 0)
            for area in (zone.get('areas') or []):
                shape_type = area.get('shape')
                if not shape_type:
                    continue
                area_pos = area.get('position') or {}
                base_x = zone_x + area_pos.get('x', 0)
                base_y = zone_y + area_pos.get('y', 0)
                data = {
                    'type': shape_type,
                    'x': base_x,
                    'y': base_y,
                    'color': area.get('color'),
                    'border_color': area.get('border_color'),
                    'rotation': area.get('rotation') or 0,
                }
                if shape_type == 'rectangle':
                    rect = area.get('rectangle') or {}
                    width = rect.get('width') or 0
                    height = rect.get('height') or 0
                    data.update({'width': width, 'height': height})
                    self._extend_bounds(bounds, base_x, base_y)
                    self._extend_bounds(bounds, base_x + width, base_y + height)
                elif shape_type == 'circle':
                    radius = (area.get('circle') or {}).get('radius') or 0
                    data['radius'] = radius
                    self._extend_bounds(bounds, base_x - radius, base_y - radius)
                    self._extend_bounds(bounds, base_x + radius, base_y + radius)
                elif shape_type == 'ellipse':
                    radius = (area.get('ellipse') or {}).get('radius') or {}
                    radius_x = radius.get('x', 0)
                    radius_y = radius.get('y', 0)
                    data['radius_x'] = radius_x
                    data['radius_y'] = radius_y
                    self._extend_bounds(bounds, base_x - radius_x, base_y - radius_y)
                    self._extend_bounds(bounds, base_x + radius_x, base_y + radius_y)
                elif shape_type == 'polygon':
                    points = []
                    for point in (area.get('polygon') or {}).get('points') or []:
                        px = base_x + point.get('x', 0)
                        py = base_y + point.get('y', 0)
                        points.append({'x': px, 'y': py})
                        self._extend_bounds(bounds, px, py)
                    data['points'] = points
                elif shape_type == 'text':
                    text_def = area.get('text') or {}
                    text_pos = text_def.get('position') or {}
                    tx = base_x + text_pos.get('x', 0)
                    ty = base_y + text_pos.get('y', 0)
                    data.update({
                        'text': text_def.get('text', ''),
                        'text_color': text_def.get('color'),
                        'text_size': text_def.get('size'),
                        'text_x': tx,
                        'text_y': ty,
                    })
                    self._extend_bounds(bounds, tx, ty)
                if shape_type != 'text':
                    self._apply_area_label(area, data, base_x, base_y, bounds)
                shapes.append(data)
        return shapes

    def _apply_area_label(self, area, shape_data, base_x, base_y, bounds):
        label_info = self._area_label(area)
        if not label_info:
            return
        label = label_info['text']
        label_position = label_info.get('position') or area.get('label_position') or {}
        label_style = label_info.get('style') or area.get('label_style') or {}
        if label_position:
            label_x = base_x + label_position.get('x', 0)
            label_y = base_y + label_position.get('y', 0)
        else:
            label_x, label_y = self._shape_label_center(shape_data, base_x, base_y)
        shape_data.update({
            'label': label,
            'label_x': label_x,
            'label_y': label_y,
        })
        if label_style.get('color'):
            shape_data['label_color'] = label_style.get('color')
        if label_style.get('size'):
            shape_data['label_size'] = label_style.get('size')
        self._extend_bounds(bounds, label_x, label_y)

    @staticmethod
    def _area_label(area):
        text_def = area.get('text')
        if isinstance(text_def, dict):
            value = (text_def.get('text') or '').strip()
            if value:
                return {
                    'text': value,
                    'position': text_def.get('position'),
                    'style': {
                        'color': text_def.get('color'),
                        'size': text_def.get('size'),
                    },
                }
        for key in ('label', 'name', 'text'):
            raw = area.get(key)
            if isinstance(raw, str):
                raw = raw.strip()
            else:
                continue
            if raw:
                return {'text': raw}
        return None

    @staticmethod
    def _shape_label_center(shape_data, base_x, base_y):
        shape_type = shape_data.get('type')
        if shape_type == 'rectangle':
            width = shape_data.get('width') or 0
            height = shape_data.get('height') or 0
            return base_x + width / 2, base_y + height / 2
        if shape_type == 'circle':
            return base_x, base_y
        if shape_type == 'ellipse':
            return base_x, base_y
        if shape_type == 'polygon':
            points = shape_data.get('points') or []
            if points:
                xs = [point.get('x', 0) for point in points]
                ys = [point.get('y', 0) for point in points]
                return sum(xs) / len(xs), sum(ys) / len(ys)
        return base_x, base_y


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(allow_cors_if_namespaced, name='dispatch')
class SeatAssignmentView(EventViewMixin, CartMixin, View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        if not request.event.settings.get('quse_seatingplan_checkout_enabled', as_type=bool):
            return self._json_error(_('Seat selection is disabled for this event.'), status=404)
        payload = self._read_payload()
        if payload is None:
            return self._json_error(_('Invalid payload.'), status=400)
        cart_position_id = payload.get('cart_position')
        if not cart_position_id:
            return self._json_error(_('Cart position is required.'), status=400)
        try:
            cart_position = self._cart_position(cart_position_id)
        except Http404:
            return self._json_error(_('Ticket could not be found.'), status=404)
        cart_id = get_or_create_cart_id(request)
        if cart_position.cart_id != cart_id:
            return self._json_error(_('This ticket is not part of your cart.'), status=403)
        seat_guid = payload.get('seat_guid')
        subevent = cart_position.subevent
        allowed_products = set(
            SeatCategoryMapping.objects.filter(event=request.event, subevent=subevent).values_list('product_id', flat=True)
        )
        if cart_position.item_id not in allowed_products:
            return self._json_error(_('This product is not connected to the seating plan.'), status=400)
        if seat_guid:
            response, status_code = self._assign_seat(cart_position, seat_guid, subevent)
            return JsonResponse(response, status=status_code)
        else:
            cart_position.seat = None
            cart_position.save(update_fields=['seat'])
            response = {'cart_position': cart_position.pk, 'seat_guid': None}
            return JsonResponse(response)

    def _read_payload(self):
        try:
            return json.loads(self.request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None

    def _cart_position(self, pk):
        if not pk:
            return None
        try:
            return CartPosition.objects.select_related('seat', 'subevent').get(pk=pk, event=self.request.event)
        except CartPosition.DoesNotExist as exc:
            raise Http404() from exc

    def _assign_seat(self, cart_position, seat_guid, subevent):
        seat_qs = self.request.event.seats.filter(subevent=subevent)
        with transaction.atomic():
            try:
                seat = seat_qs.select_for_update().get(seat_guid=seat_guid)
            except Seat.DoesNotExist:
                return {'error': _('Seat could not be found.')}, 404
            if seat.product_id and seat.product_id != cart_position.item_id:
                return {'error': _('Seat belongs to a different product.')}, 409
            if not seat.is_available(ignore_cart=cart_position):
                return {'error': _('Seat is already taken.')}, 409
            cart_position.seat = seat
            cart_position.save(update_fields=['seat'])
        return {
            'cart_position': cart_position.pk,
            'seat_guid': cart_position.seat.seat_guid if cart_position.seat else None,
        }, 200

    @staticmethod
    def _json_error(message, status=400):
        return JsonResponse({'error': message}, status=status)
