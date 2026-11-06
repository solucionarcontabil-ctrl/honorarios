"""Telas do portal, vistas pelo cliente."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from cobranca import pix
from cobranca.models import Cobranca, Configuracao

from . import services
from .forms import EnviarClienteForm
from .models import CATEGORIAS, Arquivo, Protocolo


def portal(view):
    """Exige login de cliente e define a empresa ativa (request.cliente)."""
    @wraps(view)
    def interna(request, *args, **kwargs):
        acesso = getattr(request, "acesso_portal", None)
        if not request.user.is_authenticated:
            return redirect(f"/entrar/?next={request.path}")
        if not acesso:
            return redirect("painel")
        empresas = list(acesso.clientes.filter(ativo=True))
        if not empresas:
            return render(request, "portal/sem_empresa.html")
        escolhida = request.session.get("portal_cliente")
        if escolhida is None and len(empresas) > 1:
            # sem escolha ainda: abre na empresa que tem documento novo
            com_novidade = Arquivo.objects.filter(cliente__in=empresas, direcao=Arquivo.ENVIADO,
                                                  visualizado_em__isnull=True).values_list("cliente_id", flat=True).first()
            escolhida = com_novidade
        request.cliente = next((c for c in empresas if c.pk == escolhida), empresas[0])
        request.empresas = empresas
        return view(request, *args, **kwargs)
    return interna


@portal
def trocar_empresa(request, cliente_id):
    if any(c.pk == cliente_id for c in request.empresas):
        request.session["portal_cliente"] = cliente_id
    return redirect("portal_inicio")


@portal
def inicio(request):
    c = request.cliente
    hoje = timezone.localdate()
    if not request.session.get("portal_logado"):
        request.session["portal_logado"] = True
        services.log(request, "login", c)
    docs = c.arquivos.filter(direcao=Arquivo.ENVIADO)
    return render(request, "portal/cliente_inicio.html", {
        "novos": docs.filter(visualizado_em__isnull=True)[:10],
        "guias": docs.filter(categoria="GUIA", vencimento__gte=hoje).order_by("vencimento")[:10],
        "honorarios": c.cobrancas.filter(status=Cobranca.ABERTA).order_by("vencimento")[:6],
        "pendentes_confirmar": c.protocolos.filter(tipo=Protocolo.ENTREGA, confirmado_em__isnull=True).count(),
        "config": Configuracao.atual(),
    })


@portal
def documentos(request):
    qs = request.cliente.arquivos.filter(direcao=Arquivo.ENVIADO)
    cat = request.GET.get("categoria")
    if cat:
        qs = qs.filter(categoria=cat)
    usados = set(request.cliente.arquivos.filter(direcao=Arquivo.ENVIADO).values_list("categoria", flat=True))
    return render(request, "portal/cliente_documentos.html", {
        "arquivos": qs[:300], "categorias": [c for c in CATEGORIAS if c[0] in usados], "cat": cat,
        "enviados": request.cliente.arquivos.filter(direcao=Arquivo.RECEBIDO)[:50],
    })


@portal
def baixar(request, pk):
    a = get_object_or_404(Arquivo, pk=pk, cliente=request.cliente)
    try:
        resp = FileResponse(a.arquivo.open("rb"), as_attachment=True, filename=a.nome_original)
    except FileNotFoundError:
        raise Http404("Arquivo não encontrado.")
    services.registrar_download(a, request)
    return resp


@portal
def enviar(request):
    form = EnviarClienteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        arquivos = request.FILES.getlist("arquivos")
        if not arquivos:
            messages.error(request, "Selecione ao menos um arquivo.")
        else:
            d = form.cleaned_data
            try:
                prot, criados, lote = services.receber(request.cliente, arquivos, request.user, d["categoria"],
                                                       d["competencia"], d["observacao"], ip=services.ip_de(request))
            except services.ArquivoRecusado as e:
                messages.error(request, str(e))
            else:
                services.log(request, "envio", request.cliente, f"{len(criados)} arquivo(s) — {prot.codigo}")
                msg = f"Recebemos {len(criados)} arquivo(s). Protocolo {prot.codigo}."
                if lote:
                    msg += f" {lote.novos} nota(s) fiscal(is) lida(s)."
                messages.success(request, msg)
                return redirect("portal_protocolo", pk=prot.pk)
    return render(request, "portal/cliente_enviar.html", {"form": form})


@portal
def honorarios(request):
    config = Configuracao.atual()
    abertas = list(request.cliente.cobrancas.filter(status=Cobranca.ABERTA).order_by("vencimento"))
    for c in abertas:
        c.qr = mark_safe(pix.qrcode_svg(c.pix_copia_cola)) if c.pix_copia_cola else ""
        c.atualizado = c.valor_atualizado(config=config)
    return render(request, "portal/cliente_honorarios.html", {
        "abertas": abertas,
        "pagas": request.cliente.cobrancas.filter(status=Cobranca.PAGA).order_by("-data_pagamento")[:24],
        "config": config,
    })


@portal
def recibo(request, pk):
    c = get_object_or_404(Cobranca, pk=pk, cliente=request.cliente, status=Cobranca.PAGA)
    return render(request, "cobranca/recibo.html", {"c": c, "config": Configuracao.atual()})


@portal
def protocolos(request):
    return render(request, "portal/cliente_protocolos.html", {
        "protocolos": request.cliente.protocolos.prefetch_related("itens")[:100],
    })


@portal
def protocolo(request, pk):
    p = get_object_or_404(Protocolo, pk=pk, cliente=request.cliente)
    return render(request, "portal/cliente_protocolo.html", {"p": p, "itens": p.itens.select_related("arquivo"),
                                                             "config": Configuracao.atual()})


@portal
@require_POST
def protocolo_confirmar(request, pk):
    p = get_object_or_404(Protocolo, pk=pk, cliente=request.cliente, tipo=Protocolo.ENTREGA)
    if p.confirmar("CLIQUE", request.user, services.ip_de(request)):
        services.log(request, "confirmou", request.cliente, p.codigo)
        messages.success(request, "Recebimento confirmado. Obrigado!")
    return redirect("portal_protocolo", pk=pk)


@portal
def senha(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    for f in form.fields.values():
        f.widget.attrs["class"] = "form-control"
    if request.method == "POST" and form.is_valid():
        form.save()
        update_session_auth_hash(request, form.user)
        services.log(request, "trocou senha", request.cliente)
        messages.success(request, "Senha alterada.")
        return redirect("portal_inicio")
    return render(request, "portal/cliente_senha.html", {"form": form})
