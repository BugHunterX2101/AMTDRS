from src.pricing import apply_coupon


def seasonal_price(total):
    return apply_coupon(total, 10)
