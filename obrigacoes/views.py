from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from cobranca.importacao import analisar, importar
from cobranca.models import Cliente

from . import services
from .calendario import feriados_nacionais
from .forms import (CertificadoForm, EntregaEditarForm, FeriadoForm, ImportarClientesForm, LerPfxForm,
                    ObrigacaoForm, TarefaForm)
from .models import Certificado, ClienteObrigacao, Entrega, Feriado, Obrigacao


def _mes(valor):
    try:
        ano, mes = (valor or "").split("-")
        return date(int(ano), int(mes), 1)
    except ValueError:
        return services.mes_atual()


def _seguro(url, padrao):
    return url if url and url.startswith("/") and not url.startswith("//") else padrao


def _usuarios():
    return get_user_model().objects.filter(is_active=True)


# ------------------------------------------------------------------ painel de obrigações
@login_required
def entregas(request):
    hoje = timezone.localdate()
    mes = _mes(request.GET.get("mes"))
    fim = date(mes.year + (mes.month == 12), mes.month % 12 + 1, 1)
    visao = request.GET.get("visao", "mes")

    qs = Entrega.objects.select_related("cliente", "obrigacao", "responsavel", "concluido_por")
    if visao == "atrasadas":
        qs = qs.filter(status__in=[Entrega.PENDENTE, Entrega.ANDAMENTO], prazo__lt=hoje)
    elif visao == "semana":
        qs = qs.filter(status__in=[Entrega.PENDENTE, Entrega.ANDAMENTO], prazo__gte=hoje, prazo__lte=hoje + timedelta(days=7))
    else:
        qs = qs.filter(prazo__gte=mes, prazo__lt=fim)

    resp = request.GET.get("responsavel", "")
    if resp == "eu":
        qs = qs.filter(responsavel=request.user)
    elif resp.isdigit():
        qs = qs.filter(responsavel_id=resp)
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET["status"])
    if request.GET.get("obrigacao"):
        qs = qs.filter(obrigacao_id=request.GET["obrigacao"])
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(cliente__nome__icontains=q) | Q(cliente__nome_fantasia__icontains=q) | Q(titulo__icontains=q))

    abertas = Entrega.objects.filter(status__in=[Entrega.PENDENTE, Entrega.ANDAMENTO])
    do_mes = Entrega.objects.filter(prazo__gte=mes, prazo__lt=fim)
    total_mes = do_mes.count()
    feitas_mes = do_mes.filter(status__in=[Entrega.ENTREGUE, Entrega.DISPENSADA]).count()
    pendentes_gerar = len([i for i in services.previa_mes(mes.year, mes.month) if not i["ja_existe"]])

    return render(request, "obrigacoes/entregas.html", {
        "entregas": qs.order_by("prazo", "cliente__nome", "titulo")[:800],
        "mes": mes, "visao": visao, "filtros": request.GET,
        "usuarios": _usuarios(), "obrigacoes": Obrigacao.objects.filter(ativa=True),
        "status_opcoes": Entrega.STATUS,
        "qtd_atrasadas": abertas.filter(prazo__lt=hoje).count(),
        "qtd_hoje": abertas.filter(prazo=hoje).count(),
        "qtd_semana": abertas.filter(prazo__gte=hoje, prazo__lte=hoje + timedelta(days=7)).count(),
        "total_mes": total_mes, "feitas_mes": feitas_mes,
        "perc_mes": int(feitas_mes * 100 / total_mes) if total_mes else 0,
        "pendentes_gerar": pendentes_gerar,
        "mes_anterior": date(mes.year - (mes.month == 1), (mes.month - 2) % 12 + 1, 1),
        "mes_seguinte": fim,
    })


@login_required
@require_POST
def entrega_acao(request, pk, acao):
    e = get_object_or_404(Entrega, pk=pk)
    if acao == "entregar":
        e.concluir(request.user, Entrega.ENTREGUE, request.POST.get("protocolo", "").strip())
    elif acao == "dispensar":
        e.concluir(request.user, Entrega.DISPENSADA)
    elif acao == "andamento":
        e.status = Entrega.ANDAMENTO
        e.save(update_fields=["status"])
    elif acao == "reabrir":
        e.reabrir()
    return redirect(_seguro(request.POST.get("proximo"), "/obrigacoes/"))


@login_required
@require_POST
def entregas_lote(request):
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
    acao = request.POST.get("acao")
    status = {"entregar": Entrega.ENTREGUE, "dispensar": Entrega.DISPENSADA}.get(acao)
    n = 0
    if status:
        for e in Entrega.objects.filter(pk__in=ids, status__in=[Entrega.PENDENTE, Entrega.ANDAMENTO]):
            e.concluir(request.user, status)
            n += 1
    elif acao and acao.startswith("resp_"):
        n = Entrega.objects.filter(pk__in=ids).update(responsavel_id=acao[5:] or None)
    messages.success(request, f"{n} item(ns) atualizado(s).")
    return redirect(_seguro(request.POST.get("proximo"), "/obrigacoes/"))


@login_required
def entrega_editar(request, pk):
    e = get_object_or_404(Entrega, pk=pk)
    form = EntregaEditarForm(request.POST or None, instance=e)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if obj.status in (Entrega.ENTREGUE, Entrega.DISPENSADA) and not obj.concluido_em:
            obj.concluido_em, obj.concluido_por = timezone.now(), request.user
        elif obj.aberta:
            obj.concluido_em = obj.concluido_por = None
        obj.save()
        messages.success(request, "Salvo.")
        return redirect(_seguro(request.GET.get("proximo"), "/obrigacoes/"))
    return render(request, "cobranca/form.html", {
        "form": form, "titulo": f"{e.titulo} — {e.cliente or 'escritório'}",
        "voltar": _seguro(request.GET.get("proximo"), "/obrigacoes/"),
    })


@login_required
def tarefa_nova(request):
    form = TarefaForm(request.POST or None, initial={
        "cliente": request.GET.get("cliente"), "responsavel": request.user, "prazo": timezone.localdate(),
    })
    if request.method == "POST" and form.is_valid():
        t = form.save(commit=False)
        t.criado_por = request.user
        t.save()
        messages.success(request, "Tarefa criada.")
        return redirect(f"/obrigacoes/?mes={t.prazo:%Y-%m}")
    return render(request, "cobranca/form.html", {"form": form, "titulo": "Nova tarefa", "voltar": "/obrigacoes/"})


@login_required
def gerar(request):
    mes = _mes(request.POST.get("mes") or request.GET.get("mes"))
    if request.method == "POST":
        criadas = services.gerar_mes(mes.year, mes.month, request.user)
        messages.success(request, f"{len(criadas)} obrigação(ões) gerada(s) com vencimento em {mes:%m/%Y}.")
        return redirect(f"/obrigacoes/?mes={mes:%Y-%m}")
    itens = services.previa_mes(mes.year, mes.month)
    sem_vinculo = Cliente.objects.filter(ativo=True).annotate(n=Count("obrigacoes")).filter(n=0)
    return render(request, "obrigacoes/gerar.html", {
        "mes": mes, "itens": itens, "qtd_novo": len([i for i in itens if not i["ja_existe"]]),
        "sem_vinculo": sem_vinculo, "catalogo_vazio": not Obrigacao.objects.exists(),
    })


# ------------------------------------------------------------------ catálogo
@login_required
def catalogo(request):
    if request.method == "POST" and request.POST.get("acao") == "padrao":
        n = services.carregar_catalogo_padrao()
        messages.success(request, f"{n} obrigação(ões) padrão adicionada(s). Confira os prazos.")
        return redirect("obrig_catalogo")
    return render(request, "obrigacoes/catalogo.html", {
        "obrigacoes": Obrigacao.objects.annotate(qtd=Count("clientes")),
        "feriados": Feriado.objects.filter(data__year__gte=timezone.localdate().year - 1),
        "nacionais": sorted(feriados_nacionais(timezone.localdate().year).items()),
        "aviso": services.AVISO_PRAZOS,
    })


@login_required
def obrigacao_form(request, pk=None):
    ob = get_object_or_404(Obrigacao, pk=pk) if pk else None
    form = ObrigacaoForm(request.POST or None, instance=ob)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Obrigação salva. Itens já gerados não mudam; os próximos usam o novo prazo.")
        return redirect("obrig_catalogo")
    return render(request, "cobranca/form.html", {
        "form": form, "titulo": f"Obrigação {ob}" if ob else "Nova obrigação", "voltar": "/obrigacoes/catalogo/",
    })


@login_required
def feriado_form(request):
    form = FeriadoForm(request.POST or None)
    if request.method == "POST":
        if request.POST.get("excluir"):
            Feriado.objects.filter(pk=request.POST["excluir"]).delete()
            return redirect("obrig_catalogo")
        if form.is_valid():
            form.save()
            messages.success(request, "Feriado cadastrado.")
            return redirect("obrig_catalogo")
    return render(request, "cobranca/form.html", {
        "form": form, "titulo": "Feriado estadual / municipal", "voltar": "/obrigacoes/catalogo/",
    })


# ------------------------------------------------------------------ obrigações do cliente
@login_required
def cliente_obrigacoes(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    if request.method == "POST":
        if request.POST.get("acao") == "padrao":
            n = services.aplicar_padrao_regime(cliente, substituir=bool(request.POST.get("substituir")))
            messages.success(request, f"{n} obrigação(ões) vinculada(s) pelo regime.")
        else:
            marcadas = {int(i) for i in request.POST.getlist("obrigacoes") if i.isdigit()}
            cliente.obrigacoes.exclude(obrigacao_id__in=marcadas).delete()
            for oid in marcadas:
                ClienteObrigacao.objects.get_or_create(cliente=cliente, obrigacao_id=oid)
            messages.success(request, "Obrigações do cliente atualizadas.")
        return redirect("cliente_detalhe", pk=cliente.pk)
    vinculadas = set(cliente.obrigacoes.values_list("obrigacao_id", flat=True))
    return render(request, "obrigacoes/cliente_obrigacoes.html", {
        "cliente": cliente, "obrigacoes": Obrigacao.objects.filter(ativa=True), "vinculadas": vinculadas,
    })


# ------------------------------------------------------------------ certificados
@login_required
def certificados(request):
    situacao = request.GET.get("situacao", "ativos")
    qs = Certificado.objects.select_related("cliente")
    hoje = timezone.localdate()
    if situacao == "ativos":
        qs = qs.filter(ativo=True)
    elif situacao == "vencendo":
        qs = qs.filter(ativo=True, validade_fim__lte=hoje + timedelta(days=30))
    sem_cert = Cliente.objects.filter(ativo=True, certificados__isnull=True).exclude(regime="PF")
    return render(request, "obrigacoes/certificados.html", {
        "certificados": qs, "situacao": situacao, "sem_certificado": sem_cert,
        "pfx_form": LerPfxForm(),
    })


@login_required
def certificado_form(request, pk=None):
    cert = get_object_or_404(Certificado, pk=pk) if pk else None
    form = CertificadoForm(request.POST or None, instance=cert, initial={"cliente": request.GET.get("cliente")})
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Certificado salvo.")
        return redirect("certificados")
    return render(request, "cobranca/form.html", {
        "form": form, "titulo": "Certificado digital", "voltar": "/certificados/",
    })


@login_required
@require_POST
def certificado_ler_pfx(request):
    form = LerPfxForm(request.POST, request.FILES)
    if not form.is_valid():
        for erros in form.errors.values():
            messages.error(request, " ".join(erros))
        return redirect("certificados")
    try:
        dados = services.ler_pfx(form.cleaned_data["arquivo"].read(), form.cleaned_data["senha"])
    except services.CertificadoInvalido as e:
        messages.error(request, str(e))
        return redirect("certificados")
    cliente = services.cliente_por_documento(dados["documento"])
    modelo = "ECPF" if len(dados["documento"]) == 11 else "ECNPJ"
    # substitui o certificado anterior do mesmo titular
    antigos = Certificado.objects.filter(documento=dados["documento"], ativo=True, validade_fim__lt=dados["validade_fim"])
    n_antigos = antigos.update(ativo=False) if dados["documento"] else 0
    cert, criado = Certificado.objects.update_or_create(
        documento=dados["documento"], validade_fim=dados["validade_fim"], tipo="A1",
        defaults={**dados, "cliente": cliente, "modelo": modelo, "ativo": True,
                  "onde_esta": form.cleaned_data["onde_esta"]},
    )
    msg = f"Certificado de {cert.titular} lido: válido até {cert.validade_fim:%d/%m/%Y}."
    if not cliente:
        msg += " Não achei o cliente pelo CNPJ/CPF; vincule manualmente."
    if n_antigos:
        msg += f" {n_antigos} certificado(s) antigo(s) marcado(s) como substituído(s)."
    messages.success(request, msg + " O arquivo não foi guardado.")
    return redirect("certificados")


# ------------------------------------------------------------------ importar clientes
@login_required
def importar_clientes(request):
    if request.method == "POST" and request.POST.get("confirmar"):
        dados = request.session.pop("importacao", None)
        if not dados:
            messages.error(request, "A prévia expirou. Envie a planilha de novo.")
            return redirect("importar_clientes")
        responsavel = _usuarios().filter(pk=dados["responsavel"]).first() if dados["responsavel"] else None
        criados, atualizados = importar(dados["linhas"], responsavel)
        vinculadas = 0
        if dados["aplicar"]:
            docs = [l["dados"]["documento"] for l in dados["linhas"] if l["situacao"] == "novo"]
            for c in Cliente.objects.filter(documento__in=docs):
                vinculadas += services.aplicar_padrao_regime(c)
        messages.success(request, f"{criados} cliente(s) criado(s), {atualizados} completado(s)"
                         + (f", {vinculadas} vínculo(s) de obrigação." if dados["aplicar"] else "."))
        return redirect("clientes")

    form = ImportarClientesForm(request.POST or None, request.FILES or None)
    contexto = {"form": form}
    if request.method == "POST" and form.is_valid():
        arq = form.cleaned_data["arquivo"]
        try:
            linhas, mapa, erro = analisar(arq.name, arq.read())
        except Exception as e:  # planilha corrompida, formato estranho
            linhas, mapa, erro = [], {}, f"Não consegui ler o arquivo: {e}"
        if erro:
            messages.error(request, erro)
        else:
            request.session["importacao"] = {
                "linhas": linhas,
                "responsavel": form.cleaned_data["responsavel"].pk if form.cleaned_data["responsavel"] else None,
                "aplicar": form.cleaned_data["aplicar_obrigacoes"],
            }
            contexto.update({
                "linhas": linhas, "colunas": sorted(mapa),
                "qtd": {s: len([l for l in linhas if l["situacao"] == s]) for s in ("novo", "existe", "erro")},
            })
    return render(request, "obrigacoes/importar.html", contexto)
