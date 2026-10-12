from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class DiamondsConfig(AppConfig):
    name = "apps.diamonds"
    label = "diamonds"
    verbose_name = "Diamonds and gemstones"

    def ready(self):
        from apps.core.documents import register_document

        register_document("diamonds.StoneSetting", "stone-setting", _("Stone setting"))
        register_document("diamonds.SettingJob", "setting-job", _("Setter job"))
        register_document("diamonds.BuyBack", "buy-back", _("Old diamonds bought"))
        register_document("diamonds.ParcelDeal", "parcel-deal", _("Parcel stones"))
        register_document("diamonds.ParcelTransfer", "parcel-transfer",
                          _("Stones moved between branches"))
        register_document("diamonds.ParcelCount", "parcel-count", _("Parcels counted"))
        register_document("diamonds.StoneRemoval", "stone-removal", _("Stones taken out"))
        register_document("diamonds.StoneHandout", "stone-handout", _("Stones with a workshop"))
