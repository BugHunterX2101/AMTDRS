"""Order pricing. `calc_total` and `apply_coupon` are the interfaces under migration."""

TAX_RATE = 0.2


def calc_total(items, tax=TAX_RATE):
    """Line prices summed, plus tax."""
    subtotal = sum(price * qty for price, qty in items)
    return round(subtotal * (1 + tax), 2)


def apply_coupon(total, percent):
    """`total` reduced by `percent` per cent."""
    return round(total * (1 - percent / 100), 2)
