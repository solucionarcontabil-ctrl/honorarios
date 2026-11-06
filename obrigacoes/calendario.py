"""Dias úteis e feriados (nacionais + os cadastrados pelo escritório)."""
import calendar
from datetime import date, timedelta
from functools import lru_cache


def pascoa(ano):
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = (h + l - 7 * m + 114) % 31 + 1
    return date(ano, mes, dia)


@lru_cache(maxsize=64)
def feriados_nacionais(ano):
    p = pascoa(ano)
    fixos = {
        date(ano, 1, 1): "Confraternização Universal",
        date(ano, 4, 21): "Tiradentes",
        date(ano, 5, 1): "Dia do Trabalho",
        date(ano, 9, 7): "Independência",
        date(ano, 10, 12): "Nossa Senhora Aparecida",
        date(ano, 11, 2): "Finados",
        date(ano, 11, 15): "Proclamação da República",
        date(ano, 11, 20): "Consciência Negra",
        date(ano, 12, 25): "Natal",
        p - timedelta(days=48): "Carnaval (segunda)",
        p - timedelta(days=47): "Carnaval (terça)",
        p - timedelta(days=2): "Sexta-feira Santa",
        p + timedelta(days=60): "Corpus Christi",
    }
    return fixos


def _feriados_extras():
    try:
        from .models import Feriado
        return set(Feriado.objects.values_list("data", flat=True))
    except Exception:  # banco ainda não migrado
        return set()


class Calendario:
    """Instancie uma vez por operação (carrega os feriados do banco uma vez)."""

    def __init__(self, extras=None):
        self.extras = _feriados_extras() if extras is None else set(extras)

    def dia_util(self, d):
        return d.weekday() < 5 and d not in feriados_nacionais(d.year) and d not in self.extras

    def proximo_util(self, d):
        while not self.dia_util(d):
            d += timedelta(days=1)
        return d

    def anterior_util(self, d):
        while not self.dia_util(d):
            d -= timedelta(days=1)
        return d

    def enesimo_dia_util(self, ano, mes, n):
        d, cont = date(ano, mes, 1), 0
        while d.month == mes:
            if self.dia_util(d):
                cont += 1
                if cont == n:
                    return d
            d += timedelta(days=1)
        return self.ultimo_dia_util(ano, mes)

    def ultimo_dia_util(self, ano, mes):
        return self.anterior_util(date(ano, mes, calendar.monthrange(ano, mes)[1]))

    def ajustar(self, d, regra):
        if regra == "ANTECIPA":
            return self.anterior_util(d)
        if regra == "POSTERGA":
            return self.proximo_util(d)
        return d
