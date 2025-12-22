from urllib.parse import urlencode

from pretix.multidomain.urlreverse import eventreverse


def build_seatingframe_url(event, subevent=None, cart_namespace=None, voucher_code=None):
    """Return the iframe URL for the plugin-managed seating plan view."""
    kwargs = {}
    if subevent:
        kwargs['subevent'] = getattr(subevent, 'pk', subevent)
    if cart_namespace:
        kwargs['cart_namespace'] = cart_namespace
    base_url = eventreverse(event, 'plugins:quse_seatingplan:frame', kwargs=kwargs)
    query = {'iframe': '1'}
    if voucher_code:
        query['voucher'] = voucher_code
    return f"{base_url}?{urlencode(query)}"
