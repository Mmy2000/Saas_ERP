"""JSON rendering that never turns a Decimal into a float (§6.4).

Serializer DecimalFields already emit strings (COERCE_DECIMAL_TO_STRING); this covers every
other path, such as views that build a plain dict of computed totals.
"""

from decimal import Decimal

from rest_framework.renderers import JSONRenderer as DRFJSONRenderer
from rest_framework.utils.encoders import JSONEncoder


class DecimalSafeEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


class JSONRenderer(DRFJSONRenderer):
    encoder_class = DecimalSafeEncoder
