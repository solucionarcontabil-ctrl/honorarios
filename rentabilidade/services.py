from collections import defaultdict
from datetime import date
from decimal import Decimal


from cobranca.models import Cliente, Cobranca, Configuracao

from .models import Apontamento, Membro

ZERO = Decimal("0")


def custos_hora():
    return dict(Membro.objects.values_list("usuario_id", "custo_hora"))


def parse_tempo(texto):
    """Aceita '90', '1:30', '1h30', '1,5h', '2h'. Retorna minutos ou None."""
    t = (texto or "").strip().lower().replace(" ", "")
    if not t:
        return None
    try:
        if ":" in t or ("h" in t and not t.endswith("h")):
            h, m = t.replace("h", ":").split(":", 1)
            return int(h or 0) * 60 + int(m.replace("min", "") or 0)
        if t.endswith("h"):
            return round(float(t[:-1].replace(",", ".")) * 60)
        return int(t.replace("min", ""))
    except ValueError:
        return None


def relatorio(inicio, fim, config=None):
    """Rentabilidade por cliente entre dois meses (inclusive). Receita = honorários por competência."""
    config = config or Configuracao.atual()
    custos = custos_hora()
    fim_exclusivo = date(fim.year + (fim.month == 12), fim.month % 12 + 1, 1)

    receita = defaultdict(lambda: ZERO)
    recebido = defaultdict(lambda: ZERO)
    for cid, valor, pago in (Cobranca.objects.filter(competencia__gte=inicio, competencia__lt=fim_exclusivo)
                             .exclude(status=Cobranca.CANCELADA).values_list("cliente_id", "valor", "valor_pago")):
        receita[cid] += valor
        recebido[cid] += pago or ZERO

    minutos = defaultdict(int)
    custo = defaultdict(lambda: ZERO)
    sem_custo = set()
    for cid, uid, m in (Apontamento.objects.filter(data__gte=inicio, data__lt=fim_exclusivo, em_andamento=False)
                        .values_list("cliente_id", "usuario_id", "minutos")):
        minutos[cid] += m
        ch = custos.get(uid) or ZERO
        if not ch:
            sem_custo.add(uid)
        custo[cid] += ch * m / 60

    ids = {k for k in list(receita) + list(minutos) if k is not None}
    clientes = Cliente.objects.in_bulk(ids)
    alvo = config.margem_alvo_percentual / 100
    linhas = []
    for cid in ids:
        r, c, m = receita[cid], custo[cid].quantize(Decimal("0.01")), minutos[cid]
        margem = r - c
        perc = (margem / r * 100) if r else None
        meses = (fim.year - inicio.year) * 12 + fim.month - inicio.month + 1
        sugerido = (c / (1 - alvo) / meses).quantize(Decimal("0.01")) if c and alvo < 1 else None
        mensal_atual = (r / meses).quantize(Decimal("0.01"))
        linhas.append({
            "cliente": clientes[cid], "receita": r, "recebido": recebido[cid], "minutos": m,
            "horas": f"{m // 60}h{m % 60:02d}", "custo": c, "margem": margem, "perc": perc,
            "por_hora": (r / Decimal(m) * 60).quantize(Decimal("0.01")) if m else None,
            "mensal_atual": mensal_atual,
            "sugerido": sugerido if sugerido and sugerido > mensal_atual else None,
        })
    linhas.sort(key=lambda l: (l["perc"] if l["perc"] is not None else Decimal("-9999")))
    interno = minutos.get(None, 0)
    totais = {
        "receita": sum((l["receita"] for l in linhas), ZERO), "custo": sum((l["custo"] for l in linhas), ZERO),
        "minutos": sum(l["minutos"] for l in linhas), "interno_min": interno,
        "interno_custo": custo.get(None, ZERO).quantize(Decimal("0.01")),
    }
    totais["margem"] = totais["receita"] - totais["custo"]
    totais["perc"] = (totais["margem"] / totais["receita"] * 100) if totais["receita"] else None
    totais["horas"] = f"{totais['minutos'] // 60}h{totais['minutos'] % 60:02d}"
    totais["interno_horas"] = f"{interno // 60}h{interno % 60:02d}"
    return linhas, totais, sem_custo


def detalhe_cliente(cliente, inicio, fim):
    fim_exclusivo = date(fim.year + (fim.month == 12), fim.month % 12 + 1, 1)
    qs = Apontamento.objects.filter(cliente=cliente, data__gte=inicio, data__lt=fim_exclusivo, em_andamento=False)
    custos = custos_hora()
    por_atividade = defaultdict(int)
    por_usuario = defaultdict(int)
    for a in qs.select_related("usuario"):
        por_atividade[a.get_atividade_display()] += a.minutos
        por_usuario[a.usuario.get_full_name() or a.usuario.username, a.usuario_id] += a.minutos
    fmt = lambda m: f"{m // 60}h{m % 60:02d}"  # noqa: E731
    return {
        "apontamentos": qs.select_related("usuario", "entrega")[:300],
        "por_atividade": sorted(((k, fmt(v), v) for k, v in por_atividade.items()), key=lambda x: -x[2]),
        "por_usuario": sorted(((k[0], fmt(v), (custos.get(k[1]) or ZERO) * v / 60) for k, v in por_usuario.items()),
                              key=lambda x: -x[2]),
    }
