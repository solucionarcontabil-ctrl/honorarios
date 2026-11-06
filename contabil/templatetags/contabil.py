from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def dc(valor):
    """Saldo com indicador D (devedor) ou C (credor)."""
    v = Decimal(valor or 0)
    if not v:
        return "0,00"
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {'D' if v > 0 else 'C'}"
