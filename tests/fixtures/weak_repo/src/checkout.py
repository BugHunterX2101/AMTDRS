from src.pricing import apply_coupon, calc_total


def checkout(cart, coupon=None):
    total = calc_total(cart)
    if coupon:
        total = apply_coupon(total, coupon)
    return total
