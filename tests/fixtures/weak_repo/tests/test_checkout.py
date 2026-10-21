from src.checkout import checkout
from src.reports import report_title


def test_checkout_total():
    assert checkout([(10.0, 2)]) == 24.0


def test_checkout_with_coupon():
    assert checkout([(10.0, 2)], coupon=50) == 12.0


def test_report_title():
    assert report_title(3) == "Revenue Q3"
