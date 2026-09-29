from decimal import Decimal

from apps.core.api.renderers import JSONRenderer


def test_decimals_render_as_strings_not_floats():
    rendered = JSONRenderer().render({"amount": Decimal("0.10"), "big": Decimal("425000.000000")})
    assert rendered == b'{"amount":"0.10","big":"425000.000000"}'
