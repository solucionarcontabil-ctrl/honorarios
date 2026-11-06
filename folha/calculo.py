"""Cálculos trabalhistas e previdenciários (funções puras, testáveis isoladamente)."""
import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

D = Decimal
ZERO = D("0")


def r2(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def brl(v):
    return "R$ " + f"{D(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ------------------------------------------------------------------ INSS
def inss_empregado(base, tabela):
    """Contribuição progressiva por faixas (EC 103/2019), limitada ao teto."""
    base = min(D(base), D(tabela["teto"]))
    total, anterior, memoria = ZERO, ZERO, []
    for limite, aliq in tabela["faixas"]:
        limite, aliq = D(limite), D(aliq)
        if base <= anterior:
            break
        parte = min(base, limite) - anterior
        valor = parte * aliq / 100
        total += valor
        memoria.append(f"{brl(parte)} × {aliq}% = {valor:.4f}".replace(".", ","))
        anterior = limite
    return r2(total), memoria


def inss_contribuinte_individual(base, tabela, aliquota=D("11")):
    """Pró-labore: 11% limitado ao teto (empresa paga a cota patronal)."""
    b = min(D(base), D(tabela["teto"]))
    return r2(b * aliquota / 100)


# ------------------------------------------------------------------ IRRF
def irrf(rendimento, inss, dependentes, pensao, tabela, redutor=True, simplificado=True):
    """IRRF mensal: usa a maior entre as deduções legais (INSS + dependentes + pensão) e o desconto
    simplificado; aplica a tabela progressiva e, se houver na tabela vigente, a redução da Lei 15.270/2025."""
    rendimento = D(rendimento)
    legais = D(inss) + D(tabela["dependente"]) * dependentes + D(pensao)
    simp = D(tabela["simplificado"]) if simplificado else ZERO
    deducao = max(legais, simp)
    forma = "desconto simplificado" if simp > legais else "deduções legais"
    base = max(rendimento - deducao, ZERO)
    imposto, faixa_txt = ZERO, "isento"
    for limite, aliq, parcela in tabela["faixas"]:
        if limite is None or base <= D(limite):
            imposto = max(base * D(aliq) / 100 - D(parcela), ZERO)
            faixa_txt = f"{aliq}% − {brl(parcela)}"
            break
    imposto = r2(imposto)
    memoria = [f"Rendimento tributável {brl(rendimento)} − {forma} {brl(deducao)} = base {brl(base)}; "
               f"tabela: {faixa_txt} → {brl(imposto)}"]
    reducao = ZERO
    red = tabela.get("redutor") if redutor else None
    if red and imposto > 0:
        if rendimento <= D(red["isencao_ate"]):
            reducao = min(imposto, D(red["reducao_maxima"]))
        elif rendimento <= D(red["faixa_ate"]):
            reducao = min(imposto, max(D(red["constante"]) - D(red["fator"]) * rendimento, ZERO))
        reducao = r2(reducao)
        if reducao:
            memoria.append(f"Redução da Lei 15.270/2025: − {brl(reducao)}")
    final = max(imposto - reducao, ZERO)
    return r2(final), r2(base), memoria


# ------------------------------------------------------------------ dias e proporções
def ultimo_dia(ano, mes):
    return calendar.monthrange(ano, mes)[1]


def dias_mes_comercial(competencia, inicio, fim=None):
    """Dias de salário no mês: mês inteiro = 30; admissão/desligamento no mês = dias corridos trabalhados
    (limitado a 30; desligamento no último dia do mês, com o mês todo trabalhado, = 30)."""
    ult = ultimo_dia(competencia.year, competencia.month)
    fim_mes = date(competencia.year, competencia.month, ult)
    if inicio > fim_mes or (fim and fim < competencia):
        return 0
    adm_no_mes = inicio >= competencia
    dem_no_mes = bool(fim and fim <= fim_mes)
    if not adm_no_mes and not dem_no_mes:
        return 30
    a = inicio.day if adm_no_mes else 1
    b = fim.day if dem_no_mes else ult
    if not adm_no_mes and b == ult:
        return 30
    return max(min(b - a + 1, 30), 0)


def dias_uteis_e_dsr(competencia):
    """Para o DSR: dias úteis (seg. a sáb. sem feriado) e domingos + feriados do mês."""
    from obrigacoes.calendario import Calendario, feriados_nacionais
    cal = Calendario()
    feriados = set(feriados_nacionais(competencia.year)) | cal.extras
    uteis = dsr = 0
    for dia in range(1, ultimo_dia(competencia.year, competencia.month) + 1):
        d = date(competencia.year, competencia.month, dia)
        if d.weekday() == 6 or d in feriados:
            dsr += 1
        else:
            uteis += 1
    return uteis, dsr


def avos_13(admissao, ate, ano):
    """Avos do 13º no ano: mês com 15 dias ou mais de trabalho conta 1/12."""
    avos = 0
    for mes in range(1, 13):
        ini = max(date(ano, mes, 1), admissao)
        fim = min(date(ano, mes, ultimo_dia(ano, mes)), ate)
        if fim >= ini and (fim - ini).days + 1 >= 15:
            avos += 1
    return avos


def somar_meses_data(d, n):
    t = d.year * 12 + d.month - 1 + n
    a, m = t // 12, t % 12 + 1
    return date(a, m, min(d.day, ultimo_dia(a, m)))


def avos_ferias(inicio_aquisitivo, ate):
    """Avos de férias proporcionais: cada mês completo do período aquisitivo e fração de 15 dias ou mais."""
    meses, cursor = 0, inicio_aquisitivo
    while True:
        prox = somar_meses_data(inicio_aquisitivo, meses + 1)
        if prox - timedelta(days=1) <= ate:
            meses += 1
            cursor = prox
        else:
            break
    resto = (ate - cursor).days + 1
    if resto >= 15:
        meses += 1
    return min(meses, 12)


def periodos_aquisitivos(admissao, ate):
    """Lista de (início, fim) de períodos aquisitivos COMPLETOS até a data."""
    out, ini = [], admissao
    while True:
        fim = somar_meses_data(ini, 12) - timedelta(days=1)
        if fim > ate:
            return out, ini  # ini = início do período em curso
        out.append((ini, fim))
        ini = fim + timedelta(days=1)


def anos_completos(inicio, fim):
    return fim.year - inicio.year - ((fim.month, fim.day) < (inicio.month, inicio.day))


def dias_aviso_previo(admissao, data_aviso):
    """Lei 12.506/2011: 30 dias + 3 por ano completo de serviço, até 90 dias."""
    return min(30 + 3 * max(anos_completos(admissao, data_aviso), 0), 90)
