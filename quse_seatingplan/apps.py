from django.utils.translation import gettext_lazy

from . import __version__

try:
    from pretix.base.plugins import PluginConfig
except ImportError:
    raise RuntimeError("Please use pretix 2.7 or above to run this plugin!")


class PluginApp(PluginConfig):
    default = True
    name = "quse_seatingplan"
    verbose_name = "Seating Plan"

    class PretixPluginMeta:
        name = gettext_lazy("Seating Plan")
        author = "QUSE"
        description = gettext_lazy(
            "Upload seating plans and enable seat selection during checkout."
        )
        visible = True
        version = __version__
        category = "FEATURE"
        compatibility = "pretix>=2.7.0"
        settings_links = [
            (
                (gettext_lazy("Settings"), gettext_lazy("Seating plan")),
                "plugins:quse_seatingplan:settings",
                {},
            ),
        ]
        navigation_links = []

    def ready(self):
        from . import patches  # NOQA
        from . import signals  # NOQA
