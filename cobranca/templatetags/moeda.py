from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def brl(valor):
    try:
        v = Decimal(valor or 0)
    except (InvalidOperation, TypeError):
        return valor
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


@register.filter
def mesano(d):
    return d.strftime("%m/%Y") if d else ""
