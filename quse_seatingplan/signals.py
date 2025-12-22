from django.dispatch import receiver
from django.template.loader import render_to_string
from django.urls import Resolver404, resolve, reverse
from django.utils.translation import gettext_lazy as _

from pretix.control.signals import nav_event_settings
from pretix.multidomain.urlreverse import eventreverse
from pretix.presale.signals import checkout_flow_steps, render_seating_plan

from .checkout import SeatingPlanCheckoutStep


@receiver(nav_event_settings, dispatch_uid="quse_seatingplan_nav_settings")
def seatingplan_settings_link(sender, request=None, **kwargs):
    if not request or not getattr(request, 'event', None):
        return []
    if not request.user.has_event_permission(
        request.organizer,
        request.event,
        'can_change_settings',
        request=request,
    ):
        return []

    try:
        resolved = resolve(request.path_info)
    except Resolver404:
        resolved = None

    return [{
        'label': _('Seating plan'),
        'url': reverse('plugins:quse_seatingplan:settings', kwargs={
            'organizer': request.organizer.slug,
            'event': request.event.slug,
        }),
        'active': bool(
            resolved
            and resolved.namespace == 'plugins:quse_seatingplan'
            and resolved.url_name == 'settings'
        ),
    }]



@receiver(checkout_flow_steps, dispatch_uid="quse_seatingplan_checkout_step")
def register_checkout_step(sender, **kwargs):
    return SeatingPlanCheckoutStep


@receiver(render_seating_plan, dispatch_uid="quse_seatingplan_render_plan")
def render_checkout_seating(sender, request=None, subevent=None, **kwargs):
    if not request:
        return ''
    event = request.event
    if not event.settings.get('quse_seatingplan_checkout_enabled', as_type=bool):
        return ''
    owner = subevent or event
    if not owner.seating_plan:
        return ''
    route_kwargs = _build_route_kwargs(request, subevent)
    try:
        data_url = eventreverse(event, 'plugins:quse_seatingplan:seat-data', kwargs=route_kwargs)
        assign_url = eventreverse(event, 'plugins:quse_seatingplan:seat-assign', kwargs=route_kwargs)
    except Exception:
        return ''
    context = {
        'data_url': data_url,
        'assign_url': assign_url,
    }
    return render_to_string('quse_seatingplan/seatingframe.html', context, request=request)


def _build_route_kwargs(request, subevent):
    kwargs = {}
    if request.resolver_match:
        cart_namespace = request.resolver_match.kwargs.get('cart_namespace')
        if cart_namespace is not None:
            kwargs['cart_namespace'] = cart_namespace
    if subevent:
        kwargs['subevent'] = subevent.pk
    return kwargs
