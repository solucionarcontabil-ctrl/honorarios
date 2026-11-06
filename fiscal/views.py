from datetime import date
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from cobranca.forms import DATA, BootstrapMixin
from cobranca.models import Cliente
from contabil.views import valor_br

from . import services
from . import tabelas as T
from .models import Apuracao, FolhaMensal, PerfilFiscal, ReceitaMensal


class PerfilForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = PerfilFiscal
        exclude = ["cliente"]
        widgets = {"data_inicio_atividade": DATA, "observacoes": forms.Textarea(attrs={"rows": 2})}


def _mes(valor, padrao=None):
    try:
        a, m = (valor or "").split("-")[:2]
        return date(int(a), int(m), 1)
    except ValueError:
        if padrao:
            return padrao
        hoje = timezone.localdate()
        return date(hoje.year - (hoje.month == 1), (hoje.month - 2) % 12 + 1, 1)


def _eh_simples(c):
    return c.regime in ("SIMPLES", "MEI")


@login_required
def inicio(request):
    clientes = (Cliente.objects.filter(ativo=True, regime__in=["SIMPLES", "PRESUMIDO"])
                .annotate(ultima=Max("apuracoes__competencia")))
    return render(request, "fiscal/inicio.html", {"clientes": clientes})


@login_required
def cliente(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    pf = services.perfil(c)
    form = PerfilForm(request.POST or None, instance=pf)
    if request.method == "POST" and request.POST.get("parte") == "perfil":
        if form.is_valid():
            form.save()
            messages.success(request, "Perfil fiscal salvo.")
            return redirect("fiscal_cliente", cid=c.pk)
    elif request.method == "POST" and request.POST.get("parte") == "receitas":
        n = 0
        for chave, bruto in request.POST.items():
            if chave.startswith("r__"):
                _, comp, tipo = chave.split("__")
                v = valor_br(bruto)
                competencia = _mes(comp)
                if v is None and not bruto.strip():
                    ReceitaMensal.objects.filter(cliente=c, competencia=competencia, tipo=tipo, origem="MANUAL").delete()
                    continue
                if v is None:
                    continue
                atual = ReceitaMensal.objects.filter(cliente=c, competencia=competencia, tipo=tipo).first()
                if atual and atual.valor == v:
                    continue
                ReceitaMensal.objects.update_or_create(cliente=c, competencia=competencia, tipo=tipo,
                                                       defaults={"valor": v, "origem": "MANUAL", "alterado_por": request.user})
                n += 1
            elif chave.startswith("f__"):
                competencia = _mes(chave[3:])
                v = valor_br(bruto)
                if v is None:
                    FolhaMensal.objects.filter(cliente=c, competencia=competencia).delete()
                else:
                    FolhaMensal.objects.update_or_create(cliente=c, competencia=competencia, defaults={"valor": v})
        messages.success(request, f"Receitas salvas ({n} alteração(ões)).")
        return redirect("fiscal_cliente", cid=c.pk)
    ate = _mes(request.GET.get("ate"), timezone.localdate().replace(day=1))
    meses = [services.somar_meses(ate, -i) for i in range(12, -1, -1)]
    simples = _eh_simples(c)
    tipos = [t for t in (T.TIPOS_SIMPLES if simples else T.TIPOS_PRESUMIDO)]
    usados = set(ReceitaMensal.objects.filter(cliente=c, competencia__in=meses).values_list("tipo", flat=True))
    padrao = {pf.anexo_mercadorias, pf.anexo_servicos} if simples else {pf.atividade_mercadorias, pf.atividade_servicos}
    tipos = [t for t in tipos if t[0] in usados or t[0] in padrao]
    valores = {(r.competencia, r.tipo): r for r in ReceitaMensal.objects.filter(cliente=c, competencia__in=meses)}
    folhas = {f.competencia: f.valor for f in FolhaMensal.objects.filter(cliente=c, competencia__in=meses)}
    grade = []
    for m in meses:
        celulas = [(t[0], valores.get((m, t[0]))) for t in tipos]
        grade.append({"mes": m, "celulas": celulas, "folha": folhas.get(m),
                      "total": sum((r.valor for _, r in celulas if r), Decimal(0))})
    return render(request, "fiscal/cliente.html", {
        "c": c, "form": form, "simples": simples, "tipos": tipos, "grade": grade, "ate": ate,
        "apuracoes": c.apuracoes.select_related("conferida_por")[:24],
    })


@login_required
@require_POST
def importar_xml(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    comp = _mes(request.POST.get("competencia"))
    feitos = services.importar_receitas_xml(c, comp, request.user)
    if feitos:
        messages.success(request, f"Receitas de {comp:%m/%Y} montadas a partir das notas: "
                         + ", ".join(f"{dict(T.TIPOS_RECEITA)[k]} {services.brl(v)}" for k, v in feitos.items()))
    else:
        messages.warning(request, f"Nenhuma nota de venda/serviço autorizada em {comp:%m/%Y} na Central de XMLs.")
    return redirect("fiscal_cliente", cid=c.pk)


@login_required
def simples(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    comp = _mes(request.GET.get("competencia") or request.POST.get("competencia"))
    resultado = services.calcular_simples(c, comp)
    ap = Apuracao.objects.filter(cliente=c, tipo="SIMPLES", competencia=comp).first()
    if request.method == "POST":
        if request.POST.get("acao") == "salvar":
            ap = services.salvar(c, "SIMPLES", comp, resultado, request.user)
            messages.success(request, "Apuração salva.")
        elif request.POST.get("acao") == "conferir" and ap:
            services.conferir(ap, request.user)
            messages.success(request, "Apuração marcada como conferida.")
        return redirect(f"/fiscal/{c.pk}/simples/?competencia={comp:%Y-%m}")
    return render(request, "fiscal/simples.html", {"c": c, "comp": comp, "r": resultado, "ap": ap,
                                                   "nomes": dict(T.TIPOS_RECEITA), "diferente": ap and str(ap.total) != resultado["total"]})


@login_required
def presumido(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    comp = _mes(request.GET.get("competencia") or request.POST.get("competencia"))
    tri = (comp.month - 1) // 3 + 1
    inicio_tri = date(comp.year, (tri - 1) * 3 + 1, 1)
    params = request.POST if request.method == "POST" else request.GET
    outras = valor_br(params.get("outras")) or Decimal(0)
    irrf = valor_br(params.get("irrf")) or Decimal(0)
    csll_ret = valor_br(params.get("csll_retida")) or Decimal(0)
    meses_at = int(params.get("meses") or 3) if str(params.get("meses") or "3") in "123" else 3
    mensal = services.calcular_presumido_mensal(c, comp)
    trimestral = services.calcular_presumido_trimestral(c, comp.year, tri, outras, irrf, csll_ret, meses_at)
    ap_m = Apuracao.objects.filter(cliente=c, tipo="LP_MENSAL", competencia=comp).first()
    ap_t = Apuracao.objects.filter(cliente=c, tipo="LP_TRIMESTRAL", competencia=inicio_tri).first()
    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "salvar_mensal":
            services.salvar(c, "LP_MENSAL", comp, mensal, request.user)
        elif acao == "salvar_trimestral":
            services.salvar(c, "LP_TRIMESTRAL", inicio_tri, trimestral, request.user)
        elif acao == "conferir_mensal" and ap_m:
            services.conferir(ap_m, request.user)
        elif acao == "conferir_trimestral" and ap_t:
            services.conferir(ap_t, request.user)
        messages.success(request, "Salvo.")
        return redirect(f"/fiscal/{c.pk}/presumido/?competencia={comp:%Y-%m}")
    return render(request, "fiscal/presumido.html", {
        "c": c, "comp": comp, "tri": tri, "m": mensal, "t": trimestral, "ap_m": ap_m, "ap_t": ap_t,
        "fim_tri": comp.month % 3 == 0, "params": params, "nomes": dict(T.TIPOS_RECEITA)})


@login_required
def cfop(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    comp = _mes(request.GET.get("competencia"))
    return render(request, "fiscal/cfop.html", {"c": c, "comp": comp, "r": services.resumo_cfop(c, comp)})


@login_required
def tabelas(request):
    return render(request, "fiscal/tabelas.html", {"anexos": T.ANEXOS, "presumido": T.ATIVIDADES_PRESUMIDO, "T": T})
