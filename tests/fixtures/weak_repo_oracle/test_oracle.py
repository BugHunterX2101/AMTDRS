"""The suite the repository should have had.

Kept outside the fixture on purpose. Principal never sees it: it is not in the
tree that is cloned, graphed, tested or mutated. It exists to answer one question
independently of Principal: after a patch, does the code still behave? A patch is
correct if and only if this passes.
"""

from src.checkout import checkout
from src.legacy_export import export_rows
from src.promotions import seasonal_price
from src.reports import quarterly_revenue


def test_checkout_total():
    assert checkout([(10.0, 2)]) == 24.0


def test_checkout_with_coupon():
    assert checkout([(10.0, 2)], coupon=50) == 12.0


def test_export_rows():
    assert export_rows([[(10.0, 1)]]) == [{"total": 12.0}]


def test_quarterly_revenue_uses_pricing_without_tax():
    assert quarterly_revenue([[(10.0, 1)], [(5.0, 2)]]) == 20.0


def test_seasonal_price_takes_ten_percent_off():
    assert seasonal_price(100.0) == 90.0
