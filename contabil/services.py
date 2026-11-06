"""Regras contábeis: validação de lançamentos, períodos fechados, estorno, saldos e relatórios."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from .models import Conta, Fechamento, Lancamento, LogContabil, Partida

ZERO = Decimal("0.00")


class ErroContabil(ValidationError):
    pass


# ------------------------------------------------------------------ período fechado
def fechado_ate(cliente):
    f = Fechamento.objects.filter(cliente=cliente).first()
    return f.fechado_ate if f else None


def verificar_periodo(cliente, data):
    limite = fechado_ate(cliente)
    if limite and data <= limite:
        raise ErroContabil(f"Período fechado até {limite:%d/%m/%Y}. Para corrigir, faça um estorno com data posterior.")


@transaction.atomic
def fechar_periodo(cliente, ate, usuario, ip=None):
    anterior = fechado_ate(cliente)
    if anterior and ate < anterior:
        raise ErroContabil("Para reabrir um período, use a reabertura (somente administrador).")
    Fechamento.objects.update_or_create(cliente=cliente, defaults={"fechado_ate": ate, "fechado_por": usuario})
    registrar(cliente, usuario, "fechar_periodo", f"até {ate:%d/%m/%Y}",
              {"fechado_ate": str(anterior) if anterior else None}, {"fechado_ate": str(ate)}, ip)


@transaction.atomic
def reabrir_periodo(cliente, ate, usuario, motivo, ip=None):
    """Reabertura excepcional (ex.: retificação de ECD). Exige motivo e fica na auditoria."""
    if not usuario.is_staff:
        raise ErroContabil("Somente administradores podem reabrir períodos.")
    if not motivo.strip():
        raise ErroContabil("Informe o motivo da reabertura.")
    anterior = fechado_ate(cliente)
    if ate:
        Fechamento.objects.update_or_create(cliente=cliente, defaults={"fechado_ate": ate, "fechado_por": usuario})
    else:
        Fechamento.objects.filter(cliente=cliente).delete()
    registrar(cliente, usuario, "reabrir_periodo", motivo[:80],
              {"fechado_ate": str(anterior) if anterior else None}, {"fechado_ate": str(ate) if ate else None,
                                                                     "motivo": motivo}, ip)


# ------------------------------------------------------------------ auditoria
def foto(lanc):
    return {
        "numero": lanc.numero, "data": str(lanc.data), "historico": lanc.historico, "documento": lanc.documento,
        "partidas": [{"conta": p.conta.codigo, "tipo": p.tipo, "valor": str(p.valor), "complemento": p.complemento}
                     for p in lanc.partidas.select_related("conta")],
    }


def registrar(cliente, usuario, acao, objeto, antes=None, depois=None, ip=None):
    LogContabil.objects.create(cliente=cliente, usuario=usuario, acao=acao, objeto=objeto[:80],
                               antes=antes, depois=depois, ip=ip)


# ------------------------------------------------------------------ lançamentos
@dataclass
class PartidaDados:
    conta: Conta
    tipo: str
    valor: Decimal
    complemento: str = ""


def validar(cliente, data, partidas):
    erros = []
    if len(partidas) < 2:
        erros.append("O lançamento precisa de pelo menos um débito e um crédito.")
    deb = sum((p.valor for p in partidas if p.tipo == "D"), ZERO)
    cred = sum((p.valor for p in partidas if p.tipo == "C"), ZERO)
    if not any(p.tipo == "D" for p in partidas) or not any(p.tipo == "C" for p in partidas):
        erros.append("Precisa haver ao menos um débito e um crédito.")
    if deb != cred:
        erros.append(f"Débitos ({deb:,.2f}) diferentes dos créditos ({cred:,.2f}).".replace(",", "X").replace(".", ",").replace("X", "."))
    for p in partidas:
        if p.valor <= 0:
            erros.append("Valores devem ser maiores que zero.")
        if p.conta.cliente_id != cliente.pk:
            erros.append(f"A conta {p.conta} não pertence a este cliente.")
        if p.conta.sintetica:
            erros.append(f"A conta {p.conta} é sintética; use uma conta analítica.")
        if not p.conta.ativa:
            erros.append(f"A conta {p.conta} está inativa.")
    if data > timezone.localdate() + timedelta(days=366):
        erros.append("Data muito no futuro.")
    if erros:
        raise ErroContabil(list(dict.fromkeys(erros)))
    verificar_periodo(cliente, data)


def _proximo_numero(cliente):
    # trava as linhas do cliente para dois lançamentos simultâneos não pegarem o mesmo número
    ultimo = Lancamento.objects.select_for_update().filter(cliente=cliente).aggregate(m=Max("numero"))["m"] or 0
    return ultimo + 1


@transaction.atomic
def lancar(cliente, data, historico, partidas, usuario, documento="", origem="MANUAL", origem_ref="", ip=None,
           estorno_de=None):
    validar(cliente, data, partidas)
    if origem_ref and Lancamento.objects.filter(cliente=cliente, origem=origem, origem_ref=origem_ref).exists():
        raise ErroContabil("Esta origem já foi contabilizada.")
    lanc = Lancamento.objects.create(cliente=cliente, numero=_proximo_numero(cliente), data=data,
                                     historico=historico[:300], documento=documento[:60], origem=origem,
                                     origem_ref=origem_ref[:80], criado_por=usuario, estorno_de=estorno_de)
    Partida.objects.bulk_create([Partida(lancamento=lanc, conta=p.conta, tipo=p.tipo, valor=p.valor,
                                         complemento=p.complemento[:200]) for p in partidas])
    registrar(cliente, usuario, "incluir", f"lançamento {lanc.numero}", None, foto(lanc), ip)
    return lanc


@transaction.atomic
def alterar(lanc, data, historico, partidas, usuario, documento="", ip=None):
    verificar_periodo(lanc.cliente, lanc.data)  # a data antiga também não pode estar fechada
    if lanc.origem in ("ENCERRAMENTO", "ESTORNO"):
        raise ErroContabil("Lançamentos de encerramento e de estorno não são alterados; estorne se necessário.")
    validar(lanc.cliente, data, partidas)
    antes = foto(lanc)
    lanc.data, lanc.historico, lanc.documento = data, historico[:300], documento[:60]
    lanc.alterado_por, lanc.alterado_em = usuario, timezone.now()
    lanc.save()
    lanc.partidas.all().delete()
    Partida.objects.bulk_create([Partida(lancamento=lanc, conta=p.conta, tipo=p.tipo, valor=p.valor,
                                         complemento=p.complemento[:200]) for p in partidas])
    registrar(lanc.cliente, usuario, "alterar", f"lançamento {lanc.numero}", antes, foto(lanc), ip)
    return lanc


@transaction.atomic
def excluir(lanc, usuario, ip=None):
    verificar_periodo(lanc.cliente, lanc.data)
    if hasattr(lanc, "estornado_por"):
        raise ErroContabil("Este lançamento já foi estornado; exclua o estorno primeiro.")
    antes = foto(lanc)
    cliente, numero = lanc.cliente, lanc.numero
    from .models import ItemExtrato
    ItemExtrato.objects.filter(lancamento=lanc).update(lancamento=None, status=ItemExtrato.PENDENTE)
    lanc.delete()
    registrar(cliente, usuario, "excluir", f"lançamento {numero}", antes, None, ip)


@transaction.atomic
def estornar(lanc, data, usuario, motivo="", ip=None):
    if hasattr(lanc, "estornado_por"):
        raise ErroContabil("Lançamento já estornado.")
    if lanc.estorno_de_id:
        raise ErroContabil("Não se estorna um estorno; faça um novo lançamento correto.")
    invertidas = [PartidaDados(p.conta, "C" if p.tipo == "D" else "D", p.valor, p.complemento)
                  for p in lanc.partidas.select_related("conta")]
    return lancar(lanc.cliente, data, f"Estorno do lançamento {lanc.numero} — {motivo or lanc.historico}"[:300],
                  invertidas, usuario, lanc.documento, "ESTORNO", "", ip, estorno_de=lanc)


# ------------------------------------------------------------------ saldos
def _movimento(cliente, inicio=None, fim=None, excluir_encerramento=False):
    """{conta_id: (débitos, créditos)} no período."""
    qs = Partida.objects.filter(lancamento__cliente=cliente)
    if inicio:
        qs = qs.filter(lancamento__data__gte=inicio)
    if fim:
        qs = qs.filter(lancamento__data__lte=fim)
    if excluir_encerramento:
        qs = qs.exclude(lancamento__origem="ENCERRAMENTO")
    mov = defaultdict(lambda: [ZERO, ZERO])
    for r in qs.values("conta_id", "tipo").annotate(s=Sum("valor")).order_by():
        mov[r["conta_id"]][0 if r["tipo"] == "D" else 1] += r["s"]
    return mov


def saldo_com_sinal(conta, deb, cred):
    """Saldo positivo = na natureza da conta; negativo = invertido."""
    return (deb - cred) if conta.devedora else (cred - deb)


def balancete(cliente, inicio, fim, so_com_movimento=True):
    """Linhas com saldo anterior, débitos, créditos e saldo final; sintéticas somam as filhas."""
    contas = list(cliente.contas.all())
    ant = _movimento(cliente, fim=inicio - timedelta(days=1))
    per = _movimento(cliente, inicio, fim)
    linhas = {}
    for c in contas:
        linhas[c.codigo] = {"conta": c, "ant_d": ZERO, "ant_c": ZERO, "deb": ZERO, "cred": ZERO}
    for c in contas:
        if c.sintetica:
            continue
        a, p = ant.get(c.pk, [ZERO, ZERO]), per.get(c.pk, [ZERO, ZERO])
        partes = c.codigo.split(".")
        for i in range(1, len(partes) + 1):  # soma na própria conta e em todas as superiores
            l = linhas.get(".".join(partes[:i]))
            if l:
                l["ant_d"] += a[0]
                l["ant_c"] += a[1]
                l["deb"] += p[0]
                l["cred"] += p[1]
    resultado = []
    for codigo in sorted(linhas, key=lambda k: [int(x) if x.isdigit() else x for x in k.split(".")]):
        l = linhas[codigo]
        c = l["conta"]
        l["saldo_ant"] = l["ant_d"] - l["ant_c"]
        l["saldo"] = l["saldo_ant"] + l["deb"] - l["cred"]
        if so_com_movimento and not any((l["saldo_ant"], l["deb"], l["cred"], l["saldo"])):
            continue
        resultado.append(l)
    tot_d = sum((l["deb"] for l in resultado if not l["conta"].sintetica), ZERO)
    tot_c = sum((l["cred"] for l in resultado if not l["conta"].sintetica), ZERO)
    return resultado, tot_d, tot_c


def razao(conta, inicio, fim):
    anterior = Partida.objects.filter(conta=conta, lancamento__data__lt=inicio).aggregate(
        d=Sum("valor", filter=Q(tipo="D")), c=Sum("valor", filter=Q(tipo="C")))
    saldo = (anterior["d"] or ZERO) - (anterior["c"] or ZERO)
    linhas = []
    inicial = saldo
    for p in (Partida.objects.filter(conta=conta, lancamento__data__gte=inicio, lancamento__data__lte=fim)
              .select_related("lancamento").order_by("lancamento__data", "lancamento__numero", "id")):
        saldo += p.valor if p.tipo == "D" else -p.valor
        contrapartidas = [x.conta for x in p.lancamento.partidas.all() if x.tipo != p.tipo]
        linhas.append({"p": p, "saldo": saldo, "contra": contrapartidas[0] if len(contrapartidas) == 1 else None})
    return inicial, linhas, saldo


def dre(cliente, inicio, fim):
    """DRE do período, sem os lançamentos de encerramento (que zeram as contas de resultado)."""
    mov = _movimento(cliente, inicio, fim, excluir_encerramento=True)
    grupos = defaultdict(lambda: ZERO)
    detalhes = defaultdict(list)
    for c in cliente.contas.filter(natureza="04", sintetica=False):
        d, cr = mov.get(c.pk, [ZERO, ZERO])
        valor = cr - d  # receitas positivas, despesas negativas
        if not valor:
            continue
        grupo = c.grupo_dre or ("RECEITA_BRUTA" if c.codigo.startswith("3") else "DESP_OPERACIONAIS")
        grupos[grupo] += valor
        detalhes[grupo].append((c, valor))
    g = grupos
    receita_liquida = g["RECEITA_BRUTA"] + g["DEDUCOES"]
    lucro_bruto = receita_liquida + g["CUSTOS"]
    operacional = lucro_bruto + g["DESP_OPERACIONAIS"] + g["RECEITAS_FINANCEIRAS"] + g["DESP_FINANCEIRAS"]
    antes_ir = operacional + g["OUTRAS_RECEITAS"] + g["OUTRAS_DESPESAS"]
    liquido = antes_ir + g["IRPJ_CSLL"]
    return {"grupos": dict(g), "detalhes": dict(detalhes), "receita_liquida": receita_liquida,
            "lucro_bruto": lucro_bruto, "operacional": operacional, "antes_ir": antes_ir, "liquido": liquido}


def balanco(cliente, data_base):
    """Balanço na data. Resultado ainda não encerrado entra no PL como 'resultado do período'."""
    mov = _movimento(cliente, fim=data_base)
    contas = list(cliente.contas.filter(natureza__in=["01", "02", "03"]))
    valores = {}
    for c in contas:
        if c.sintetica:
            continue
        d, cr = mov.get(c.pk, [ZERO, ZERO])
        valores[c.codigo] = (d - cr) if c.natureza == "01" else (cr - d)
    # resultado acumulado não encerrado (todas as contas de resultado até a data)
    resultado = ZERO
    for c in cliente.contas.filter(natureza="04", sintetica=False):
        d, cr = mov.get(c.pk, [ZERO, ZERO])
        resultado += cr - d

    def somar(prefixo):
        return sum((v for k, v in valores.items() if k == prefixo or k.startswith(prefixo + ".")), ZERO)

    linhas = []
    for c in contas:
        if c.nivel > 3:
            continue
        v = somar(c.codigo)
        if c.codigo == "2.3":
            v += resultado
        if v or c.nivel <= 2:
            linhas.append({"conta": c, "valor": v})
    ativo = somar("1")
    passivo_pl = somar("2") + resultado
    return {"linhas": linhas, "ativo": ativo, "passivo_pl": passivo_pl, "resultado": resultado,
            "diferenca": ativo - passivo_pl}


def diario(cliente, inicio, fim):
    return (cliente.lancamentos.filter(data__gte=inicio, data__lte=fim)
            .prefetch_related("partidas__conta").order_by("data", "numero"))


# ------------------------------------------------------------------ encerramento
def conta_papel(cliente, papel):
    c = cliente.contas.filter(papel=papel, sintetica=False, ativa=True).first()
    if not c:
        raise ErroContabil(f"Defina no plano de contas qual conta tem o uso '{dict(PAPEIS_NOMES).get(papel, papel)}'.")
    return c


from .models import PAPEIS as PAPEIS_NOMES  # noqa: E402


@transaction.atomic
def encerrar_exercicio(cliente, ano, usuario, ip=None):
    """1) zera as contas de resultado contra a ARE; 2) transfere a ARE para lucros ou prejuízos acumulados."""
    data = date(ano, 12, 31)
    if Lancamento.objects.filter(cliente=cliente, origem="ENCERRAMENTO", origem_ref=f"ARE-{ano}").exists():
        raise ErroContabil(f"O exercício {ano} já foi encerrado.")
    are = conta_papel(cliente, "ARE")
    mov = _movimento(cliente, date(ano, 1, 1), data, excluir_encerramento=True)
    partidas, resultado = [], ZERO
    for c in cliente.contas.filter(natureza="04", sintetica=False):
        d, cr = mov.get(c.pk, [ZERO, ZERO])
        saldo = d - cr
        if saldo > 0:
            partidas.append(PartidaDados(c, "C", saldo))
        elif saldo < 0:
            partidas.append(PartidaDados(c, "D", -saldo))
        resultado -= saldo
    if not partidas:
        raise ErroContabil("Não há saldo em contas de resultado nesse exercício.")
    if resultado > 0:
        partidas.append(PartidaDados(are, "C", resultado))
    elif resultado < 0:
        partidas.append(PartidaDados(are, "D", -resultado))
    l1 = lancar(cliente, data, f"Encerramento das contas de resultado do exercício {ano}", partidas, usuario,
                origem="ENCERRAMENTO", origem_ref=f"ARE-{ano}", ip=ip)
    l2 = None
    if resultado:
        if resultado > 0:
            destino = conta_papel(cliente, "LUCROS_ACUMULADOS")
            p2 = [PartidaDados(are, "D", resultado), PartidaDados(destino, "C", resultado)]
            hist = f"Transferência do lucro do exercício {ano}"
        else:
            destino = conta_papel(cliente, "PREJUIZOS_ACUMULADOS")
            p2 = [PartidaDados(destino, "D", -resultado), PartidaDados(are, "C", -resultado)]
            hist = f"Transferência do prejuízo do exercício {ano}"
        l2 = lancar(cliente, data, hist, p2, usuario, origem="ENCERRAMENTO", origem_ref=f"TRANSF-{ano}", ip=ip)
    return l1, l2, resultado
