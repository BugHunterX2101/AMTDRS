from src.pricing import calc_total


def export_rows(orders):
    return [{"total": calc_total(items)} for items in orders]
