"""Apurações: Simples Nacional e Lucro Presumido, com memória de cálculo.

Os resultados são para conferência e preparo das guias. A transmissão oficial continua
no PGDAS-D (Simples) e na DCTFWeb/Sicalc (Presumido).
"""
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from . import tabelas as T
from .models import Apuracao, FolhaMensal, PerfilFiscal, ReceitaMensal

ZERO = Decimal("0")
CENT = Decimal("0.01")


def r2(v):
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def somar_meses(d, n):
    t = d.year * 12 + d.month - 1 + n
    return date(t // 12, t % 12 + 1, 1)


def brl(v):
    return "R$ " + f"{Decimal(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v, casas=4):
    return f"{Decimal(v):.{casas}f}".replace(".", ",") + "%"


def perfil(cliente):
    p, _ = PerfilFiscal.objects.get_or_create(cliente=cliente)
    return p


def receitas_do_mes(cliente, competencia):
    return {r.tipo: r.valor for r in ReceitaMensal.objects.filter(cliente=cliente, competencia=competencia)}


def _total_mes(cliente, mes):
    return sum(ReceitaMensal.objects.filter(cliente=cliente, competencia=mes).values_list("valor", flat=True), ZERO)


def _folha_mes(cliente, mes):
    f = FolhaMensal.objects.filter(cliente=cliente, competencia=mes).first()
    return f.valor if f else ZERO


# ------------------------------------------------------------------ importação das notas
@transaction.atomic
def importar_receitas_xml(cliente, competencia, usuario=None):
    """Monta a receita do mês a partir das notas de saída autorizadas (só CFOPs de venda/serviço).
    Linhas digitadas à mão não são sobrescritas."""
    from contabil.rotinas import valor_operacional
    from xmls.services import emitidas
    pf = perfil(cliente)
    simples = cliente.regime in ("SIMPLES", "MEI")
    tipo_merc = pf.anexo_mercadorias if simples else pf.atividade_mercadorias
    tipo_serv = pf.anexo_servicos if simples else pf.atividade_servicos
    totais = defaultdict(lambda: ZERO)
    for doc in emitidas(cliente, competencia).filter(situacao="AUTORIZADA", tipo_operacao="SAIDA"):
        v = valor_operacional(doc, entrada=False)
        if v > 0:
            totais[tipo_serv if doc.modelo in ("NFSE", "CTE") else tipo_merc] += v
    feitos = {}
    for tipo, valor in totais.items():
        r, criado = ReceitaMensal.objects.get_or_create(cliente=cliente, competencia=competencia, tipo=tipo,
                                                        defaults={"valor": valor, "origem": "XML", "alterado_por": usuario})
        if not criado and r.origem == "XML":
            r.valor, r.alterado_por = valor, usuario
            r.save(update_fields=["valor", "alterado_por", "alterado_em"])
        feitos[tipo] = r.valor
    return feitos


# ------------------------------------------------------------------ Simples Nacional
def rbt12(cliente, competencia, pf=None):
    """Receita bruta dos 12 meses anteriores, com a regra de início de atividade
    (Res. CGSN 140/2018, art. 22): no 1º mês, receita do mês × 12; do 2º ao 12º mês,
    média dos meses anteriores × 12."""
    pf = pf or perfil(cliente)
    memoria = []
    inicio = pf.data_inicio_atividade.replace(day=1) if pf.data_inicio_atividade else None
    meses_ativos = None
    if inicio and inicio <= competencia:
        meses_ativos = (competencia.year - inicio.year) * 12 + competencia.month - inicio.month
    if meses_ativos is not None and meses_ativos < 12:
        if meses_ativos == 0:
            valor = _total_mes(cliente, competencia) * 12
            memoria.append(f"Início de atividade no próprio mês: RBT12 = receita do mês × 12 = {brl(valor)}")
            return valor, memoria, True
        anteriores = [_total_mes(cliente, somar_meses(competencia, -i)) for i in range(1, meses_ativos + 1)]
        media = sum(anteriores, ZERO) / len(anteriores)
        valor = r2(media * 12)
        memoria.append(f"Início de atividade há {meses_ativos} mês(es): RBT12 = média dos meses anteriores "
                       f"({brl(media)}) × 12 = {brl(valor)}")
        return valor, memoria, True
    valor = sum((_total_mes(cliente, somar_meses(competencia, -i)) for i in range(1, 13)), ZERO)
    memoria.append(f"RBT12 (soma dos 12 meses anteriores a {competencia:%m/%Y}) = {brl(valor)}")
    return valor, memoria, False


def fator_r(cliente, competencia, rbt, proporcional, pf=None):
    folhas = [_folha_mes(cliente, somar_meses(competencia, -i)) for i in range(1, 13)]
    if proporcional:
        pf = pf or perfil(cliente)
        inicio = pf.data_inicio_atividade.replace(day=1)
        n = (competencia.year - inicio.year) * 12 + competencia.month - inicio.month
        folha12 = (sum(folhas[:n], ZERO) / n * 12) if n else _folha_mes(cliente, competencia) * 12
    else:
        folha12 = sum(folhas, ZERO)
    if not rbt:
        return None, folha12
    return (folha12 / rbt), folha12


def aliquota_efetiva(anexo, rbt):
    n, aliq, pd = T.faixa(anexo, rbt)
    if not rbt:
        return n, aliq, pd, aliq  # sem receita anterior: alíquota nominal da 1ª faixa
    efetiva = (rbt * aliq / 100 - pd) / rbt * 100
    return n, aliq, pd, efetiva


def calcular_simples(cliente, competencia):
    pf = perfil(cliente)
    receitas = {k: v for k, v in receitas_do_mes(cliente, competencia).items() if k.startswith("SN_")}
    rbt, memoria, proporcional = rbt12(cliente, competencia, pf)
    alertas = []
    if rbt > T.LIMITE_SIMPLES:
        alertas.append("RBT12 acima de R$ 4,8 milhões: verifique a exclusão do Simples Nacional.")
    elif rbt > T.SUBLIMITE_ICMS_ISS:
        alertas.append("RBT12 acima do sublimite de R$ 3,6 milhões: ICMS e ISS são recolhidos fora do DAS "
                       "(o valor abaixo inclui esses tributos e precisa ser ajustado no PGDAS-D).")
    linhas, total = [], ZERO
    fr, folha12 = None, None
    for tipo, receita in sorted(receitas.items()):
        anexo = tipo[3:]
        obs = ""
        if tipo == "SN_FATOR_R":
            fr, folha12 = fator_r(cliente, competencia, rbt, proporcional, pf)
            if fr is None:
                anexo, obs = "V", "sem RBT12 para calcular o fator R: considerado Anexo V"
            else:
                anexo = "III" if fr >= T.FATOR_R_MINIMO else "V"
                obs = f"fator R = folha 12m {brl(folha12)} ÷ RBT12 {brl(rbt)} = {pct(fr * 100, 2)} → Anexo {anexo}"
                if not FolhaMensal.objects.filter(cliente=cliente).exists():
                    alertas.append("Nenhuma folha cadastrada: o fator R ficou zero (Anexo V). Informe a folha dos 12 meses.")
        n, aliq, pd, efetiva = aliquota_efetiva(anexo, rbt)
        valor = r2(receita * efetiva / 100)
        total += valor
        linhas.append({"tipo": tipo, "anexo": anexo, "receita": str(receita), "faixa": n, "aliquota_nominal": str(aliq),
                       "parcela_deduzir": str(pd), "aliquota_efetiva": str(efetiva.quantize(Decimal("0.0001"))),
                       "valor": str(valor), "obs": obs})
        memoria.append(f"Anexo {anexo}, {n}ª faixa: ({brl(rbt)} × {pct(aliq, 2)} − {brl(pd)}) ÷ {brl(rbt)} = "
                       f"{pct(efetiva)} × receita {brl(receita)} = {brl(valor)}" + (f" ({obs})" if obs else ""))
    if not receitas:
        alertas.append("Nenhuma receita do Simples informada para o mês. Importe das notas ou digite.")
    alertas.append("Receitas com ICMS-ST, monofásicas (PIS/COFINS) ou ISS retido reduzem o DAS e precisam ser "
                   "segregadas no PGDAS-D; este cálculo considera tudo como receita normal.")
    return {"rbt12": str(rbt), "proporcional": proporcional, "linhas": linhas, "total": str(r2(total)),
            "memoria": memoria, "alertas": alertas, "fator_r": str(fr) if fr is not None else None,
            "folha12": str(folha12) if folha12 is not None else None}


# ------------------------------------------------------------------ Lucro Presumido
def _presuncao(pf, tipo):
    nome, irpj, csll = T.ATIVIDADES_PRESUMIDO[tipo]
    return nome, (pf.presuncao_irpj if pf.presuncao_irpj is not None else irpj), \
        (pf.presuncao_csll if pf.presuncao_csll is not None else csll)


def calcular_presumido_mensal(cliente, competencia):
    pf = perfil(cliente)
    receitas = {k: v for k, v in receitas_do_mes(cliente, competencia).items() if k.startswith("LP_")}
    base = sum(receitas.values(), ZERO)
    pis = r2(base * T.PIS_CUMULATIVO / 100)
    cofins = r2(base * T.COFINS_CUMULATIVO / 100)
    # ISS só nas atividades de serviço municipais (transporte intermunicipal é ICMS)
    servicos = sum((v for k, v in receitas.items()
                    if k in ("LP_SERVICOS", "LP_SERVICOS_HOSPITALARES", "LP_INTERMEDIACAO_IMOVEIS")), ZERO)
    iss = r2(servicos * pf.aliquota_iss / 100)
    memoria = [
        f"Receita bruta do mês: {brl(base)}",
        f"PIS cumulativo: {brl(base)} × {pct(T.PIS_CUMULATIVO, 2)} = {brl(pis)} (DARF {T.DARF['PIS']})",
        f"COFINS cumulativa: {brl(base)} × {pct(T.COFINS_CUMULATIVO, 2)} = {brl(cofins)} (DARF {T.DARF['COFINS']})",
        f"ISS: receita de serviços {brl(servicos)} × {pct(pf.aliquota_iss, 2)} = {brl(iss)} (guia do município)",
    ]
    alertas = ["Exclua da base vendas canceladas, devoluções, IPI e ICMS-ST destacados, e receitas com "
               "alíquota zero ou monofásicas, quando houver."]
    if not receitas:
        alertas.insert(0, "Nenhuma receita do Presumido informada para o mês.")
    return {"base": str(base), "pis": str(pis), "cofins": str(cofins), "iss": str(iss),
            "total": str(pis + cofins + iss), "memoria": memoria, "alertas": alertas,
            "receitas": {k: str(v) for k, v in receitas.items()}}


def calcular_presumido_trimestral(cliente, ano, trimestre, outras_receitas=ZERO, irrf_retido=ZERO, csll_retida=ZERO,
                                  meses_atividade=3):
    """IRPJ e CSLL trimestrais. Outras receitas (financeiras, ganhos de capital) entram integralmente na base."""
    pf = perfil(cliente)
    meses = [date(ano, (trimestre - 1) * 3 + i, 1) for i in (1, 2, 3)]
    por_tipo = defaultdict(lambda: ZERO)
    for m in meses:
        for k, v in receitas_do_mes(cliente, m).items():
            if k.startswith("LP_"):
                por_tipo[k] += v
    memoria, base_irpj, base_csll = [], ZERO, ZERO
    for tipo, receita in sorted(por_tipo.items()):
        nome, p_irpj, p_csll = _presuncao(pf, tipo)
        bi, bc = r2(receita * p_irpj / 100), r2(receita * p_csll / 100)
        base_irpj += bi
        base_csll += bc
        memoria.append(f"{nome}: receita {brl(receita)} × {pct(p_irpj, 2)} (IRPJ) = {brl(bi)}; "
                       f"× {pct(p_csll, 2)} (CSLL) = {brl(bc)}")
    if outras_receitas:
        base_irpj += outras_receitas
        base_csll += outras_receitas
        memoria.append(f"Outras receitas (financeiras, ganhos de capital etc.): + {brl(outras_receitas)} nas duas bases")
    irpj_normal = r2(base_irpj * T.IRPJ_ALIQUOTA / 100)
    limite = T.IRPJ_LIMITE_ADICIONAL_MES * meses_atividade
    adicional = r2(max(base_irpj - limite, ZERO) * T.IRPJ_ADICIONAL / 100)
    irpj = max(irpj_normal + adicional - irrf_retido, ZERO)
    csll = max(r2(base_csll * T.CSLL_ALIQUOTA / 100) - csll_retida, ZERO)
    memoria += [
        f"IRPJ: base {brl(base_irpj)} × 15% = {brl(irpj_normal)}",
        f"Adicional: ({brl(base_irpj)} − limite {brl(limite)}) × 10% = {brl(adicional)}",
        f"(−) IRRF retido: {brl(irrf_retido)} → IRPJ a pagar {brl(irpj)} (DARF {T.DARF['IRPJ']})",
        f"CSLL: base {brl(base_csll)} × 9% = {brl(r2(base_csll * T.CSLL_ALIQUOTA / 100))} (−) retida {brl(csll_retida)} "
        f"→ {brl(csll)} (DARF {T.DARF['CSLL']})",
    ]
    quotas = []
    for nome, valor in (("IRPJ", irpj), ("CSLL", csll)):
        if valor >= 2000:
            q = r2(valor / 3)
            quotas.append(f"{nome}: pode ser pago em até 3 quotas mensais de {brl(q)} (a 2ª e a 3ª com juros Selic + 1%).")
        elif valor:
            quotas.append(f"{nome}: abaixo de R$ 2.000, pagamento em quota única.")
    alertas = ["Confira se os percentuais de presunção do cliente continuam os da lei (houve mudanças "
               "recentes para empresas de maior faturamento) e ajuste no perfil fiscal se necessário."]
    if not por_tipo:
        alertas.insert(0, "Nenhuma receita do Presumido no trimestre.")
    return {"base_irpj": str(base_irpj), "base_csll": str(base_csll), "irpj": str(irpj), "adicional": str(adicional),
            "csll": str(csll), "total": str(irpj + csll), "memoria": memoria, "quotas": quotas, "alertas": alertas,
            "receitas": {k: str(v) for k, v in por_tipo.items()},
            "parametros": {"outras": str(outras_receitas), "irrf": str(irrf_retido), "csll_retida": str(csll_retida),
                           "meses": meses_atividade}}


@transaction.atomic
def salvar(cliente, tipo, competencia, resultado, usuario):
    ap, _ = Apuracao.objects.update_or_create(
        cliente=cliente, tipo=tipo, competencia=competencia,
        defaults={"total": Decimal(resultado["total"]), "memoria": resultado, "calculada_por": usuario,
                  "conferida": False, "conferida_por": None, "conferida_em": None})
    return ap


def conferir(ap, usuario):
    ap.conferida, ap.conferida_por, ap.conferida_em = True, usuario, timezone.now()
    ap.save(update_fields=["conferida", "conferida_por", "conferida_em"])


# ------------------------------------------------------------------ resumo fiscal das notas
def resumo_cfop(cliente, competencia):
    from xmls.services import emitidas, recebidas
    out = {}
    for nome, qs in (("saidas", emitidas(cliente, competencia)), ("entradas", recebidas(cliente, competencia))):
        qs = qs.filter(situacao="AUTORIZADA")
        por_cfop = defaultdict(lambda: ZERO)
        for cfops in qs.values_list("cfops", flat=True):
            for c, v in (cfops or {}).items():
                por_cfop[c] += Decimal(v)
        tot = defaultdict(lambda: ZERO)
        for d in qs:
            for campo in ("valor_total", "valor_icms", "valor_icms_st", "valor_ipi", "valor_pis", "valor_cofins",
                          "valor_iss", "valor_ibs", "valor_cbs"):
                tot[campo] += getattr(d, campo)
        out[nome] = {"cfops": sorted(por_cfop.items()), "totais": dict(tot), "qtd": qs.count()}
    out["icms_saldo"] = out["saidas"]["totais"].get("valor_icms", ZERO) - out["entradas"]["totais"].get("valor_icms", ZERO)
    return out
