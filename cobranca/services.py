"""Regras de negócio: geração de mensalidades, reajustes, mensagens."""
import calendar
import logging
from datetime import date
from decimal import Decimal
from urllib.parse import quote

import requests
from django.db import transaction
from django.utils import timezone

from .models import Cobranca, Configuracao, Contrato, HistoricoReajuste, arred

log = logging.getLogger(__name__)

MESES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def brl(valor):
    s = f"{Decimal(valor):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def somar_meses(d, meses):
    total = d.year * 12 + (d.month - 1) + meses
    return date(total // 12, total % 12 + 1, 1)


def dia_no_mes(ano, mes, dia):
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def competencia_texto(d):
    return f"{MESES[d.month]}/{d.year}"


# --------------------------------------------------------------------------
# Geração mensal
# --------------------------------------------------------------------------
def previa_mensalidades(competencia, config=None):
    """Lista o que seria gerado para a competência (sem gravar)."""
    config = config or Configuracao.atual()
    base_venc = somar_meses(competencia, 1) if config.vencimento_mes_seguinte else competencia
    itens = []
    contratos = Contrato.objects.filter(ativo=True, cliente__ativo=True).select_related("cliente")
    for c in contratos:
        if not c.vigente_em(competencia):
            continue
        tipos = [("MENSAL", f"{c.descricao} — {competencia_texto(competencia)}")]
        if c.cobra_13 and competencia.month == c.mes_13:
            tipos.append(("13", f"13º honorário {competencia.year} — {c.descricao}"))
        for tipo, descricao in tipos:
            ja_existe = Cobranca.objects.filter(
                contrato=c, competencia=competencia, tipo=tipo
            ).exclude(status=Cobranca.CANCELADA).exists()
            itens.append({
                "contrato": c,
                "tipo": tipo,
                "descricao": descricao,
                "valor": c.valor_mensal,
                "vencimento": dia_no_mes(base_venc.year, base_venc.month, c.dia_vencimento),
                "ja_existe": ja_existe,
            })
    return itens


@transaction.atomic
def gerar_mensalidades(competencia, usuario=None, contratos_ids=None):
    criadas = []
    for item in previa_mensalidades(competencia):
        if item["ja_existe"]:
            continue
        if contratos_ids is not None and item["contrato"].id not in contratos_ids:
            continue
        criadas.append(Cobranca.objects.create(
            cliente=item["contrato"].cliente,
            contrato=item["contrato"],
            tipo=item["tipo"],
            competencia=competencia,
            descricao=item["descricao"],
            valor=item["valor"],
            vencimento=item["vencimento"],
            criado_por=usuario,
        ))
    return criadas


# --------------------------------------------------------------------------
# Reajuste (índices do Banco Central - SGS)
# --------------------------------------------------------------------------
SERIES_BCB = {"IPCA": 433, "IGPM": 189, "INPC": 188}
SERIE_SALARIO_MINIMO = 1619


class IndiceIndisponivel(Exception):
    pass


def _bcb(serie, inicio, fim):
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados"
    params = {"formato": "json", "dataInicial": inicio.strftime("%d/%m/%Y"), "dataFinal": fim.strftime("%d/%m/%Y")}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return [(d["data"], Decimal(d["valor"])) for d in r.json()]
    except Exception as e:  # rede, formato, etc.
        raise IndiceIndisponivel(f"Não foi possível consultar o Banco Central: {e}") from e


def indice_acumulado_12m(indice, mes_referencia):
    """Acumulado dos 12 meses anteriores a `mes_referencia` (date dia 1).

    Ex.: reajuste em jan/2026 usa jan/2025 a dez/2025.
    Retorna (percentual, descricao_periodo).
    """
    fim = somar_meses(mes_referencia, -1)
    inicio = somar_meses(mes_referencia, -12)
    periodo = f"{inicio:%m/%Y} a {fim:%m/%Y}"

    if indice in SERIES_BCB:
        dados = _bcb(SERIES_BCB[indice], inicio, fim)
        if len(dados) < 12:
            raise IndiceIndisponivel(
                f"{indice}: o Banco Central só tem {len(dados)} de 12 meses publicados para {periodo}."
            )
        fator = Decimal("1")
        for _, v in dados[-12:]:
            fator *= 1 + v / 100
        return ((fator - 1) * 100).quantize(Decimal("0.0001")), periodo

    if indice == "SM":
        dados = _bcb(SERIE_SALARIO_MINIMO, somar_meses(mes_referencia, -13), mes_referencia)
        if len(dados) < 2:
            raise IndiceIndisponivel("Salário mínimo: dados insuficientes.")
        anterior, atual = dados[0][1], dados[-1][1]
        return ((atual / anterior - 1) * 100).quantize(Decimal("0.0001")), f"{dados[0][0]} a {dados[-1][0]}"

    raise IndiceIndisponivel(f"Índice {indice} não é consultado automaticamente.")


@transaction.atomic
def aplicar_reajuste(contrato, percentual, periodo="", usuario=None, data=None, atualizar_abertas=False):
    data = data or timezone.localdate()
    anterior = contrato.valor_mensal
    novo = arred(anterior * (1 + Decimal(percentual) / 100))
    HistoricoReajuste.objects.create(
        contrato=contrato, data=data, indice=contrato.indice_reajuste, periodo=periodo,
        percentual=percentual, valor_anterior=anterior, valor_novo=novo, usuario=usuario,
    )
    contrato.valor_mensal = novo
    contrato.ultimo_reajuste = data
    contrato.save(update_fields=["valor_mensal", "ultimo_reajuste"])
    if atualizar_abertas:
        # só cobranças ainda não enviadas ao banco
        contrato.cobrancas.filter(
            status=Cobranca.ABERTA, vencimento__gte=data, gateway_id="", tipo="MENSAL"
        ).update(valor=novo)
    return novo


# --------------------------------------------------------------------------
# Mensagens
# --------------------------------------------------------------------------
class _Seguro(dict):
    def __missing__(self, chave):
        return "{" + chave + "}"


def montar_mensagem(cobranca, config=None):
    config = config or Configuracao.atual()
    modelo = config.mensagem_atraso if cobranca.vencida else config.mensagem_cobranca
    link = cobranca.link_pagamento
    if not link and cobranca.pix_copia_cola:
        link = f"PIX copia e cola:\n{cobranca.pix_copia_cola}"
    dados = _Seguro(
        cliente=cobranca.cliente.contato or str(cobranca.cliente),
        competencia=competencia_texto(cobranca.competencia),
        valor=brl(cobranca.valor),
        vencimento=cobranca.vencimento.strftime("%d/%m/%Y"),
        link=link or "",
        escritorio=config.nome_escritorio,
        dias_atraso=cobranca.dias_atraso(),
        valor_atualizado=brl(cobranca.valor_atualizado(config=config)),
    )
    return modelo.format_map(dados).strip()


def link_whatsapp(cobranca, config=None):
    numero = cobranca.cliente.whatsapp_internacional
    texto = quote(montar_mensagem(cobranca, config))
    if numero:
        return f"https://wa.me/{numero}?text={texto}"
    return f"https://wa.me/?text={texto}"
