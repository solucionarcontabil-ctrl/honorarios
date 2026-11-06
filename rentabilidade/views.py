from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from cobranca.equipe import equipe
from cobranca.models import Cliente, Configuracao

from . import services
from .models import ATIVIDADES, Apontamento, Membro


def _mes(valor, padrao):
    try:
        ano, mes = (valor or "").split("-")
        return date(int(ano), int(mes), 1)
    except ValueError:
        return padrao


def _seguro(url, padrao):
    return url if url and url.startswith("/") and not url.startswith("//") else padrao


@login_required
def horas(request):
    hoje = timezone.localdate()
    usuario_id = request.GET.get("usuario") if request.user.is_staff else None
    alvo = get_object_or_404(equipe(), pk=usuario_id) if usuario_id else request.user
    semana = hoje - timedelta(days=hoje.weekday())
    inicio = date.fromisoformat(request.GET["de"]) if request.GET.get("de") else semana
    fim = date.fromisoformat(request.GET["ate"]) if request.GET.get("ate") else hoje
    qs = Apontamento.objects.filter(usuario=alvo, data__gte=inicio, data__lte=fim).select_related("cliente", "entrega")
    total = sum(a.minutos for a in qs if not a.em_andamento)
    return render(request, "rentabilidade/horas.html", {
        "apontamentos": qs, "alvo": alvo, "de": inicio, "ate": fim, "hoje": hoje,
        "total": f"{total // 60}h{total % 60:02d}",
        "clientes": Cliente.objects.filter(ativo=True), "atividades": ATIVIDADES,
        "rodando": Apontamento.objects.filter(usuario=request.user, em_andamento=True).select_related("cliente").first(),
        "usuarios": equipe() if request.user.is_staff else [],
    })


@login_required
@require_POST
def lancar(request):
    minutos = services.parse_tempo(request.POST.get("tempo"))
    if not minutos or minutos > 24 * 60:
        messages.error(request, "Tempo inválido. Use 1:30, 1h30, 90 ou 1,5h.")
        return redirect(_seguro(request.POST.get("proximo"), "/horas/"))
    cliente = Cliente.objects.filter(pk=request.POST.get("cliente") or 0).first()
    try:
        data = date.fromisoformat(request.POST.get("data") or "")
    except ValueError:
        data = timezone.localdate()
    Apontamento.objects.create(usuario=request.user, cliente=cliente, data=data, minutos=minutos,
                               atividade=request.POST.get("atividade") or "OUTROS",
                               descricao=request.POST.get("descricao", "")[:200])
    messages.success(request, f"{minutos // 60}h{minutos % 60:02d} lançadas para {cliente or 'o escritório'}.")
    return redirect(_seguro(request.POST.get("proximo"), "/horas/"))


@login_required
@require_POST
def cronometro(request, acao):
    rodando = Apontamento.objects.filter(usuario=request.user, em_andamento=True)
    for a in rodando:
        a.parar()
    if acao == "iniciar":
        cliente = Cliente.objects.filter(pk=request.POST.get("cliente") or 0).first()
        Apontamento.objects.create(usuario=request.user, cliente=cliente, inicio=timezone.now(), em_andamento=True,
                                   atividade=request.POST.get("atividade") or "OUTROS",
                                   descricao=request.POST.get("descricao", "")[:200])
        messages.info(request, f"Cronômetro iniciado: {cliente or 'escritório'}.")
    elif rodando:
        messages.success(request, "Cronômetro parado e tempo lançado.")
    return redirect(_seguro(request.POST.get("proximo"), "/horas/"))


@login_required
@require_POST
def excluir(request, pk):
    a = get_object_or_404(Apontamento, pk=pk)
    if a.usuario != request.user and not request.user.is_staff:
        return HttpResponseForbidden("Só quem lançou pode excluir.")
    a.delete()
    messages.success(request, "Lançamento excluído.")
    return redirect(_seguro(request.POST.get("proximo"), "/horas/"))


@login_required
def relatorio(request):
    hoje = timezone.localdate().replace(day=1)
    ini_padrao = date(hoje.year - (hoje.month <= 3), (hoje.month - 4) % 12 + 1, 1)  # últimos 3 meses
    inicio = _mes(request.GET.get("de"), ini_padrao)
    fim = _mes(request.GET.get("ate"), date(hoje.year - (hoje.month == 1), (hoje.month - 2) % 12 + 1, 1))
    if fim < inicio:
        inicio, fim = fim, inicio
    config = Configuracao.atual()
    linhas, totais, sem_custo = services.relatorio(inicio, fim, config)
    clientes_com_horas = {l["cliente"].pk for l in linhas if l["minutos"]}
    sem_horas = [l for l in linhas if not l["minutos"]]
    return render(request, "rentabilidade/relatorio.html", {
        "linhas": [l for l in linhas if l["minutos"]], "sem_horas": sem_horas, "totais": totais, "de": inicio, "ate": fim,
        "config": config, "faltam_custos": equipe().filter(pk__in=sem_custo), "qtd_com_horas": len(clientes_com_horas),
    })


@login_required
def cliente(request, cliente_id):
    c = get_object_or_404(Cliente, pk=cliente_id)
    hoje = timezone.localdate().replace(day=1)
    inicio = _mes(request.GET.get("de"), date(hoje.year - 1 + (hoje.month == 12), hoje.month % 12 + 1, 1))
    fim = _mes(request.GET.get("ate"), hoje)
    linhas, _, _ = services.relatorio(inicio, fim)
    linha = next((l for l in linhas if l["cliente"].pk == c.pk), None)
    return render(request, "rentabilidade/cliente.html", {"c": c, "de": inicio, "ate": fim, "linha": linha,
                                                          **services.detalhe_cliente(c, inicio, fim)})


@login_required
def equipe_custos(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Somente administradores.")
    pessoas = list(equipe())
    for u in pessoas:
        Membro.objects.get_or_create(usuario=u)
    if request.method == "POST":
        for u in pessoas:
            bruto = request.POST.get(f"custo_{u.pk}", "").strip()
            if "," in bruto:
                bruto = bruto.replace(".", "").replace(",", ".")
            try:
                Membro.objects.filter(usuario=u).update(custo_hora=max(Decimal(bruto or "0"), Decimal("0")))
            except InvalidOperation:
                messages.error(request, f"Valor inválido para {u}.")
        config = Configuracao.atual()
        try:
            margem = Decimal(request.POST.get("margem", "30").replace(",", "."))
            if Decimal("0") <= margem < Decimal("100"):
                config.margem_alvo_percentual = margem
                config.save()
        except InvalidOperation:
            pass
        messages.success(request, "Custos salvos.")
        return redirect("equipe_custos")
    return render(request, "rentabilidade/equipe.html", {
        "membros": Membro.objects.filter(usuario__in=pessoas).select_related("usuario"), "config": Configuracao.atual(),
    })
