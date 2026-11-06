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


@register.filter
def get_item(d, chave):
    try:
        return d.get(chave)
    except AttributeError:
        return None


@register.filter
def virgula(valor):
    """Número com vírgula decimal (para alíquotas): 6.8061 -> 6,8061."""
    return str(valor).replace(".", ",") if valor not in (None, "") else ""
