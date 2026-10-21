"""Reports look the pricing function up by name so a deployment can swap it."""

from src import pricing


def report_title(quarter):
    return f"Revenue Q{quarter}"


def quarterly_revenue(orders):
    price = getattr(pricing, "calc_total")
    return sum(price(items, 0.0) for items in orders)
