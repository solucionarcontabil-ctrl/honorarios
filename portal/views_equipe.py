"""Telas da equipe: documentos do portal, protocolos e acessos dos clientes."""
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from cobranca.models import Cliente, Configuracao

from . import services
from .forms import AcessoForm, ProtocoloForm, PublicarForm
from .models import CATEGORIAS, AcessoPortal, Arquivo, ItemProtocolo, LogPortal, Protocolo


def _seguro(url, padrao):
    return url if url and url.startswith("/") and not url.startswith("//") else padrao


# ------------------------------------------------------------------ documentos
@login_required
def documentos(request):
    aba = request.GET.get("aba", "recebidos")
    qs = Arquivo.objects.select_related("cliente", "enviado_por", "processado_por", "visualizado_por")
    if aba == "recebidos":
        qs = qs.filter(direcao=Arquivo.RECEBIDO)
        if request.GET.get("pendentes", "1") == "1":
            qs = qs.filter(processado_em__isnull=True)
    else:
        qs = qs.filter(direcao=Arquivo.ENVIADO)
        if request.GET.get("nao_vistos") == "1":
            qs = qs.filter(visualizado_em__isnull=True)
    if request.GET.get("cliente"):
        qs = qs.filter(cliente_id=request.GET["cliente"])
    if request.GET.get("categoria"):
        qs = qs.filter(categoria=request.GET["categoria"])
    return render(request, "portal/equipe_documentos.html", {
        "arquivos": qs[:500], "aba": aba, "filtros": request.GET, "categorias": CATEGORIAS,
        "clientes": Cliente.objects.filter(ativo=True),
        "qtd_pendentes": Arquivo.objects.filter(direcao=Arquivo.RECEBIDO, processado_em__isnull=True).count(),
        "qtd_nao_vistos": Arquivo.objects.filter(direcao=Arquivo.ENVIADO, visualizado_em__isnull=True).count(),
    })


@login_required
def publicar(request):
    form = PublicarForm(request.POST or None, initial={"cliente": request.GET.get("cliente"),
                                                       "categoria": request.GET.get("categoria", "GUIA")})
    if request.method == "POST" and form.is_valid():
        arquivos = request.FILES.getlist("arquivos")
        if not arquivos:
            messages.error(request, "Selecione ao menos um arquivo.")
        else:
            d = form.cleaned_data
            try:
                prot, criados = services.publicar(d["cliente"], arquivos, request.user, d["categoria"], d["titulo"],
                                                  d["competencia"], d["vencimento"], d["valor"], d["observacao"])
            except services.ArquivoRecusado as e:
                messages.error(request, str(e))
            else:
                messages.success(request, f"{len(criados)} documento(s) publicado(s) no portal de {d['cliente']} "
                                          f"— protocolo {prot.codigo}.")
                return redirect("protocolo_detalhe", pk=prot.pk)
    return render(request, "portal/equipe_publicar.html", {"form": form})


@login_required
def baixar(request, pk):
    a = get_object_or_404(Arquivo, pk=pk)
    try:
        return FileResponse(a.arquivo.open("rb"), as_attachment=True, filename=a.nome_original)
    except FileNotFoundError:
        raise Http404("Arquivo não encontrado no disco.")


@login_required
@require_POST
def processar(request, pk):
    a = get_object_or_404(Arquivo, pk=pk, direcao=Arquivo.RECEBIDO)
    if a.processado_em:
        a.processado_em = a.processado_por = None
    else:
        a.processado_em, a.processado_por = timezone.now(), request.user
    a.save(update_fields=["processado_em", "processado_por"])
    return redirect(_seguro(request.POST.get("proximo"), "/documentos/"))


@login_required
@require_POST
def excluir_arquivo(request, pk):
    a = get_object_or_404(Arquivo, pk=pk, direcao=Arquivo.ENVIADO)
    if a.visualizado_em:
        messages.error(request, "O cliente já baixou este documento; ele não pode mais ser excluído.")
    else:
        a.arquivo.delete(save=False)
        a.itens_protocolo.update(descricao=f"{a.titulo} (removido do portal)")
        a.delete()
        messages.success(request, "Documento removido do portal.")
    return redirect(_seguro(request.POST.get("proximo"), "/documentos/?aba=enviados"))


# ------------------------------------------------------------------ protocolos
@login_required
def protocolos(request):
    qs = Protocolo.objects.select_related("cliente", "responsavel").annotate(qtd_itens=Count("itens"))
    if request.GET.get("cliente"):
        qs = qs.filter(cliente_id=request.GET["cliente"])
    if request.GET.get("tipo"):
        qs = qs.filter(tipo=request.GET["tipo"])
    situacao = request.GET.get("situacao")
    if situacao == "pendentes":
        qs = qs.filter(confirmado_em__isnull=True)
    elif situacao == "confirmados":
        qs = qs.filter(confirmado_em__isnull=False)
    q = request.GET.get("q", "").strip()
    if q:
        filtro = Q(cliente__nome__icontains=q) | Q(cliente__nome_fantasia__icontains=q) | Q(itens__descricao__icontains=q)
        if "/" in q and q.replace("/", "").isdigit():
            ano, num = q.split("/", 1)
            filtro |= Q(ano=int(ano), numero=int(num))
        qs = qs.filter(filtro).distinct()
    return render(request, "portal/protocolos.html", {
        "protocolos": qs[:500], "filtros": request.GET, "clientes": Cliente.objects.filter(ativo=True),
        "tipos": Protocolo.TIPOS,
    })


@login_required
def protocolo_novo(request):
    form = ProtocoloForm(request.POST or None, initial={"cliente": request.GET.get("cliente"), "responsavel": request.user})
    itens = []
    if request.method == "POST":
        descricoes = request.POST.getlist("item_descricao")
        comps = request.POST.getlist("item_competencia")
        qtds = request.POST.getlist("item_quantidade")
        for i, desc in enumerate(descricoes):
            if desc.strip():
                q = qtds[i] if i < len(qtds) and qtds[i].isdigit() else "1"
                itens.append((desc.strip()[:200], (comps[i] if i < len(comps) else "").strip()[:7], max(int(q), 1)))
        if not itens:
            messages.error(request, "Inclua ao menos um item no protocolo.")
        elif form.is_valid():
            prot = form.save(commit=False)
            prot.criado_por = request.user
            prot.save()
            for desc, comp, qtd in itens:
                ItemProtocolo.objects.create(protocolo=prot, descricao=desc, competencia=comp, quantidade=qtd)
            if prot.tipo == Protocolo.RECEBIMENTO:
                prot.confirmar("ESCRITORIO", request.user)
            messages.success(request, f"Protocolo {prot.codigo} criado.")
            return redirect("protocolo_detalhe", pk=prot.pk)
    return render(request, "portal/protocolo_novo.html", {"form": form, "linhas": range(6)})


@login_required
def protocolo_detalhe(request, pk):
    p = get_object_or_404(Protocolo.objects.select_related("cliente", "responsavel", "confirmado_por"), pk=pk)
    config = Configuracao.atual()
    link_whats = ""
    if p.tipo == Protocolo.ENTREGA and p.meio == "PORTAL":
        url = request.build_absolute_uri("/portal/")
        texto = (f"Olá! Publicamos {p.itens.count()} documento(s) no portal do cliente ({p.codigo}). "
                 f"Acesse: {url}\n{config.nome_escritorio}")
        link_whats = f"https://wa.me/{p.cliente.whatsapp_internacional}?text={quote(texto)}"
    return render(request, "portal/protocolo_detalhe.html", {
        "p": p, "itens": p.itens.select_related("arquivo"), "config": config, "link_whats": link_whats,
        "imprimir": request.GET.get("imprimir"),
    })


@login_required
@require_POST
def protocolo_confirmar(request, pk):
    p = get_object_or_404(Protocolo, pk=pk)
    nome = request.POST.get("contato_nome", "").strip()
    if nome:
        p.contato_nome = nome[:120]
        p.save(update_fields=["contato_nome"])
    if p.confirmar("ASSINATURA", request.user):
        messages.success(request, "Recebimento registrado (protocolo assinado).")
    return redirect("protocolo_detalhe", pk=pk)


# ------------------------------------------------------------------ acessos
@login_required
def acessos(request):
    qs = AcessoPortal.objects.select_related("usuario").prefetch_related("clientes")
    return render(request, "portal/acessos.html", {
        "acessos": qs,
        "sem_acesso": Cliente.objects.filter(ativo=True, acessos_portal__isnull=True).exclude(regime="PF")[:60],
        "logs": LogPortal.objects.select_related("usuario", "cliente")[:40],
    })


def _mensagem_acesso(request, acesso, senha):
    config = Configuracao.atual()
    url = request.build_absolute_uri("/entrar/")
    texto = (f"Olá, {acesso.nome.split()[0]}! Seu acesso ao portal do cliente da {config.nome_escritorio}:\n"
             f"Endereço: {url}\nLogin: {acesso.usuario.username}\nSenha provisória: {senha}\n"
             f"No portal você baixa guias e documentos, envia extratos e notas, e vê seus honorários.")
    numero = "".join(c for c in acesso.telefone if c.isdigit())
    if numero and not numero.startswith("55"):
        numero = "55" + numero
    return texto, f"https://wa.me/{numero}?text={quote(texto)}"


@login_required
def acesso_novo(request):
    inicial = {}
    if request.GET.get("cliente"):
        cli = Cliente.objects.filter(pk=request.GET["cliente"]).first()
        if cli:
            inicial = {"clientes": [cli.pk], "nome": cli.contato, "email": cli.email, "telefone": cli.whatsapp,
                       "login": (cli.email or cli.documento).lower()}
    form = AcessoForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        acesso, senha = services.criar_acesso(d["nome"], d["login"], d["clientes"], request.user, d["email"], d["telefone"])
        texto, link = _mensagem_acesso(request, acesso, senha)
        return render(request, "portal/acesso_criado.html", {"acesso": acesso, "senha": senha, "texto": texto, "link": link})
    return render(request, "cobranca/form.html", {"form": form, "titulo": "Novo acesso ao portal", "voltar": "/acessos/"})


@login_required
@require_POST
def acesso_acao(request, pk, acao):
    acesso = get_object_or_404(AcessoPortal, pk=pk)
    if acao == "senha":
        senha = services.nova_senha(acesso)
        texto, link = _mensagem_acesso(request, acesso, senha)
        return render(request, "portal/acesso_criado.html", {"acesso": acesso, "senha": senha, "texto": texto,
                                                             "link": link, "redefinida": True})
    if acao == "ativar":
        acesso.ativo = not acesso.ativo
        acesso.save(update_fields=["ativo"])
        messages.success(request, f"Acesso de {acesso.nome} {'ativado' if acesso.ativo else 'desativado'}.")
    return redirect("acessos")
