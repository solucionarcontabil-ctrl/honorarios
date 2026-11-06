import csv
import json
import logging
import secrets
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from cobranca.equipe import equipe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import asaas, pix, services
from .forms import BaixaForm, ClienteForm, CobrancaAvulsaForm, ConfiguracaoForm, ContratoForm
from .models import Cliente, Cobranca, Configuracao, Contrato, HistoricoReajuste

log = logging.getLogger(__name__)


def _mes(valor, padrao):
    try:
        ano, mes = (valor or "").split("-")
        return date(int(ano), int(mes), 1)
    except ValueError:
        return padrao


# ---------------------------------------------------------------- painel
@login_required
def painel(request):
    hoje = timezone.localdate()
    inicio_mes = hoje.replace(day=1)
    fim_mes = services.somar_meses(inicio_mes, 1)
    abertas = Cobranca.objects.filter(status=Cobranca.ABERTA)
    do_mes = Cobranca.objects.filter(vencimento__gte=inicio_mes, vencimento__lt=fim_mes).exclude(status=Cobranca.CANCELADA)
    atrasadas = abertas.filter(vencimento__lt=hoje)

    # últimos 12 meses: previsto x recebido por mês de vencimento
    meses = []
    for i in range(11, -1, -1):
        ini = services.somar_meses(inicio_mes, -i)
        fim = services.somar_meses(ini, 1)
        qs = Cobranca.objects.filter(vencimento__gte=ini, vencimento__lt=fim).exclude(status=Cobranca.CANCELADA)
        previsto = qs.aggregate(s=Sum("valor"))["s"] or Decimal(0)
        recebido = qs.filter(status=Cobranca.PAGA).aggregate(s=Sum("valor_pago"))["s"] or Decimal(0)
        meses.append({"mes": ini, "previsto": previsto, "recebido": recebido})
    maior = max([m["previsto"] for m in meses] + [Decimal(1)])
    for m in meses:
        m["p_prev"] = int(m["previsto"] / maior * 100)
        m["p_rec"] = int(m["recebido"] / maior * 100)

    reajustes = [c for c in Contrato.objects.filter(ativo=True).select_related("cliente") if c.reajuste_pendente(hoje)]
    competencia_atual = inicio_mes
    ctx = {
        "a_receber_mes": do_mes.filter(status=Cobranca.ABERTA).aggregate(s=Sum("valor"))["s"] or 0,
        "recebido_mes": Cobranca.objects.filter(
            status=Cobranca.PAGA, data_pagamento__gte=inicio_mes, data_pagamento__lt=fim_mes
        ).aggregate(s=Sum("valor_pago"))["s"] or 0,
        "total_atraso": atrasadas.aggregate(s=Sum("valor"))["s"] or 0,
        "qtd_inadimplentes": atrasadas.values("cliente").distinct().count(),
        "carteira_mensal": Contrato.objects.filter(ativo=True, cliente__ativo=True).aggregate(s=Sum("valor_mensal"))["s"] or 0,
        "qtd_clientes": Cliente.objects.filter(ativo=True).count(),
        "proximos": abertas.filter(vencimento__gte=hoje).select_related("cliente").order_by("vencimento")[:10],
        "atrasadas": atrasadas.select_related("cliente").order_by("vencimento")[:10],
        "meses": meses,
        "reajustes": reajustes,
        "pendente_geracao": len([i for i in services.previa_mensalidades(competencia_atual) if not i["ja_existe"]]),
        "competencia_atual": competencia_atual,
        **_resumo_escritorio(request.user, hoje),
    }
    return render(request, "cobranca/painel.html", ctx)


def _resumo_escritorio(usuario, hoje):
    from datetime import timedelta

    from obrigacoes.models import Certificado, Entrega
    from portal.models import Arquivo
    abertas = Entrega.objects.filter(status__in=[Entrega.PENDENTE, Entrega.ANDAMENTO])
    return {
        "obrig_atrasadas": abertas.filter(prazo__lt=hoje).count(),
        "obrig_minhas_semana": abertas.filter(responsavel=usuario, prazo__gte=hoje, prazo__lte=hoje + timedelta(days=7))
        .select_related("cliente").order_by("prazo")[:8],
        "docs_recebidos": Arquivo.objects.filter(direcao=Arquivo.RECEBIDO, processado_em__isnull=True).count(),
        "certs_vencendo": Certificado.objects.filter(ativo=True, validade_fim__lte=hoje + timedelta(days=30))
        .select_related("cliente").order_by("validade_fim")[:8],
    }


# ---------------------------------------------------------------- clientes
@login_required
def clientes_lista(request):
    q = request.GET.get("q", "").strip()
    situacao = request.GET.get("situacao", "ativos")
    qs = Cliente.objects.select_related("responsavel").annotate(
        qtd_atraso=Count("cobrancas", filter=Q(cobrancas__status=Cobranca.ABERTA, cobrancas__vencimento__lt=timezone.localdate())),
        mensal=Sum("contratos__valor_mensal", filter=Q(contratos__ativo=True)),
    )
    if situacao == "ativos":
        qs = qs.filter(ativo=True)
    elif situacao == "inativos":
        qs = qs.filter(ativo=False)
    if q:
        digitos = "".join(c for c in q if c.isdigit())
        filtro = Q(nome__icontains=q) | Q(nome_fantasia__icontains=q) | Q(contato__icontains=q)
        if digitos:
            filtro |= Q(documento__contains=digitos)
        qs = qs.filter(filtro)
    if request.GET.get("responsavel"):
        qs = qs.filter(responsavel_id=request.GET["responsavel"])
    return render(request, "cobranca/clientes_lista.html", {
        "clientes": qs, "q": q, "situacao": situacao,
        "usuarios": equipe(),
    })


@login_required
def cliente_form(request, pk=None):
    cliente = get_object_or_404(Cliente, pk=pk) if pk else None
    form = ClienteForm(request.POST or None, instance=cliente)
    if request.method == "POST" and form.is_valid():
        cliente = form.save()
        messages.success(request, "Cliente salvo.")
        if not pk and not cliente.contratos.exists():
            messages.info(request, "Agora cadastre o honorário (contrato) deste cliente.")
            return redirect("contrato_novo", cliente_id=cliente.pk)
        return redirect("cliente_detalhe", pk=cliente.pk)
    return render(request, "cobranca/form.html", {
        "form": form, "titulo": f"Editar {cliente}" if cliente else "Novo cliente",
        "voltar": cliente and f"/clientes/{cliente.pk}/",
    })


@login_required
def cliente_detalhe(request, pk):
    cliente = get_object_or_404(Cliente.objects.select_related("responsavel"), pk=pk)
    cobrancas = cliente.cobrancas.exclude(status=Cobranca.CANCELADA).order_by("-vencimento")
    config = Configuracao.atual()
    em_aberto = [c for c in cobrancas if c.status == Cobranca.ABERTA]
    return render(request, "cobranca/cliente_detalhe.html", {
        "cliente": cliente,
        "contratos": cliente.contratos.all(),
        "cobrancas": cobrancas[:60],
        "total_aberto": sum((c.valor for c in em_aberto), Decimal(0)),
        "total_atualizado": sum((c.valor_atualizado(config=config) for c in em_aberto if c.vencida), Decimal(0)),
        "reajustes": [r for c in cliente.contratos.all() for r in c.reajustes.all()],
        "vinculos": cliente.obrigacoes.select_related("obrigacao"),
        "certificados": cliente.certificados.filter(ativo=True),
        "entregas_abertas": cliente.entregas.filter(status__in=["PENDENTE", "ANDAMENTO"])
        .select_related("responsavel").order_by("prazo")[:20],
    })


@login_required
def contrato_form(request, cliente_id=None, pk=None):
    contrato = get_object_or_404(Contrato, pk=pk) if pk else None
    cliente = contrato.cliente if contrato else get_object_or_404(Cliente, pk=cliente_id)
    form = ContratoForm(request.POST or None, instance=contrato)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.cliente = cliente
        obj.save()
        messages.success(request, "Honorário salvo.")
        return redirect("cliente_detalhe", pk=cliente.pk)
    return render(request, "cobranca/form.html", {
        "form": form, "titulo": f"Honorário — {cliente}", "voltar": f"/clientes/{cliente.pk}/",
    })


# ---------------------------------------------------------------- cobranças
def _filtrar_cobrancas(request):
    qs = Cobranca.objects.select_related("cliente", "cliente__responsavel")
    hoje = timezone.localdate()
    situacao = request.GET.get("situacao", "abertas")
    if situacao == "abertas":
        qs = qs.filter(status=Cobranca.ABERTA)
    elif situacao == "vencidas":
        qs = qs.filter(status=Cobranca.ABERTA, vencimento__lt=hoje)
    elif situacao == "pagas":
        qs = qs.filter(status=Cobranca.PAGA)
    elif situacao == "canceladas":
        qs = qs.filter(status=Cobranca.CANCELADA)
    comp = request.GET.get("competencia")
    if comp:
        qs = qs.filter(competencia=_mes(comp, None))
    venc_de, venc_ate = request.GET.get("de"), request.GET.get("ate")
    if venc_de:
        qs = qs.filter(vencimento__gte=venc_de)
    if venc_ate:
        qs = qs.filter(vencimento__lte=venc_ate)
    if request.GET.get("cliente"):
        qs = qs.filter(cliente_id=request.GET["cliente"])
    if request.GET.get("responsavel"):
        qs = qs.filter(cliente__responsavel_id=request.GET["responsavel"])
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(cliente__nome__icontains=q) | Q(cliente__nome_fantasia__icontains=q) | Q(descricao__icontains=q))
    ordem = "-data_pagamento" if situacao == "pagas" else "vencimento"
    return qs.order_by(ordem, "cliente__nome"), situacao


@login_required
def cobrancas_lista(request):
    qs, situacao = _filtrar_cobrancas(request)
    if request.GET.get("exportar") == "csv":
        return _exportar_csv(qs)
    return render(request, "cobranca/cobrancas_lista.html", {
        "cobrancas": qs[:500],
        "total": qs.aggregate(s=Sum("valor"))["s"] or 0,
        "qtd": qs.count(),
        "situacao": situacao,
        "usuarios": equipe(),
        "filtros": request.GET,
        "query": request.GET.urlencode(),
    })


def _exportar_csv(qs):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="cobrancas.csv"'
    resp.write("﻿")  # Excel reconhece acentos
    w = csv.writer(resp, delimiter=";")
    w.writerow(["Cliente", "CNPJ/CPF", "Competência", "Descrição", "Vencimento", "Valor", "Situação",
                "Pagamento", "Valor pago", "Forma"])
    for c in qs:
        w.writerow([
            c.cliente.nome, c.cliente.documento_formatado, c.competencia.strftime("%m/%Y"), c.descricao,
            c.vencimento.strftime("%d/%m/%Y"), str(c.valor).replace(".", ","), c.situacao_display,
            c.data_pagamento.strftime("%d/%m/%Y") if c.data_pagamento else "",
            str(c.valor_pago).replace(".", ",") if c.valor_pago is not None else "",
            c.get_forma_recebimento_display(),
        ])
    return resp


@login_required
def cobranca_detalhe(request, pk):
    c = get_object_or_404(Cobranca.objects.select_related("cliente", "contrato"), pk=pk)
    config = Configuracao.atual()
    qr = mark_safe(pix.qrcode_svg(c.pix_copia_cola)) if c.pix_copia_cola else ""
    return render(request, "cobranca/cobranca_detalhe.html", {
        "c": c,
        "config": config,
        "qr": qr,
        "mensagem": services.montar_mensagem(c, config),
        "whatsapp": services.link_whatsapp(c, config),
        "baixa_form": BaixaForm(initial={
            "valor_pago": c.valor_atualizado(config=config), "forma": "PIX",
            "data_pagamento": timezone.localdate(),
        }),
        "encargos": c.encargos(config=config),
        "asaas_ok": asaas.configurado(),
        "pix_proprio_ok": bool(config.chave_pix and config.nome_recebedor_pix),
    })


@login_required
def cobranca_nova(request):
    form = CobrancaAvulsaForm(request.POST or None, initial={"cliente": request.GET.get("cliente")})
    if request.method == "POST" and form.is_valid():
        c = form.save(commit=False)
        c.tipo = "AVULSO"
        c.competencia = form.cleaned_data["competencia_mes"]
        c.criado_por = request.user
        c.save()
        messages.success(request, "Cobrança avulsa criada.")
        return redirect("cobranca_detalhe", pk=c.pk)
    return render(request, "cobranca/form.html", {"form": form, "titulo": "Nova cobrança avulsa", "voltar": "/cobrancas/"})


@login_required
@require_POST
def cobranca_baixa(request, pk):
    c = get_object_or_404(Cobranca, pk=pk)
    form = BaixaForm(request.POST)
    if c.status != Cobranca.ABERTA:
        messages.error(request, "Esta cobrança não está em aberto.")
    elif form.is_valid():
        d = form.cleaned_data
        if d["observacao"]:
            c.observacoes = (c.observacoes + "\n" + d["observacao"]).strip()
        c.registrar_pagamento(d["data_pagamento"], d["valor_pago"], d["forma"], request.user)
        messages.success(request, f"Pagamento de {c.cliente} registrado.")
    else:
        messages.error(request, "Dados da baixa inválidos.")
    proximo = request.POST.get("proximo", "")
    if proximo.startswith("/") and not proximo.startswith("//"):
        return redirect(proximo)
    return redirect("cobranca_detalhe", pk=pk)


@login_required
@require_POST
def cobranca_acao(request, pk, acao):
    c = get_object_or_404(Cobranca, pk=pk)
    config = Configuracao.atual()
    try:
        if acao == "cancelar" and c.status == Cobranca.ABERTA:
            if c.gateway_id and asaas.configurado():
                asaas.cancelar(c)
            c.status = Cobranca.CANCELADA
            c.observacoes = (c.observacoes + f"\nCancelada por {request.user} em {timezone.localdate():%d/%m/%Y}").strip()
            c.save()
            messages.success(request, "Cobrança cancelada.")
        elif acao == "reabrir" and c.status != Cobranca.ABERTA:
            c.status = Cobranca.ABERTA
            c.data_pagamento = None
            c.valor_pago = None
            c.forma_recebimento = ""
            c.observacoes = (c.observacoes + f"\nReaberta por {request.user} em {timezone.localdate():%d/%m/%Y}").strip()
            c.save()
            messages.success(request, "Cobrança reaberta.")
        elif acao == "emitir":
            asaas.emitir(c)
            messages.success(request, "Boleto/PIX emitido no Asaas.")
        elif acao == "pix":
            if not (config.chave_pix and config.nome_recebedor_pix):
                messages.error(request, "Cadastre a chave PIX e o nome do recebedor em Configurações.")
            else:
                c.pix_copia_cola = pix.gerar_payload(
                    config.chave_pix, config.nome_recebedor_pix, config.cidade,
                    valor=c.valor_atualizado(config=config) if c.vencida else c.valor,
                    txid=f"HON{c.pk}", descricao=f"Honorario {c.competencia:%m/%Y}",
                )
                c.save(update_fields=["pix_copia_cola", "atualizado_em"])
                messages.success(request, "PIX copia-e-cola gerado.")
    except asaas.AsaasErro as e:
        messages.error(request, str(e))
    return redirect("cobranca_detalhe", pk=pk)


@login_required
@require_POST
def cobrancas_emitir_lote(request):
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
    ok, erros = 0, []
    for c in Cobranca.objects.filter(pk__in=ids, status=Cobranca.ABERTA, gateway_id=""):
        try:
            asaas.emitir(c)
            ok += 1
        except asaas.AsaasErro as e:
            erros.append(f"{c.cliente}: {e}")
    if ok:
        messages.success(request, f"{ok} cobrança(s) emitida(s) no Asaas.")
    for e in erros[:10]:
        messages.error(request, e)
    proximo = request.POST.get("proximo", "")
    return redirect(proximo if proximo.startswith("/") and not proximo.startswith("//") else "cobrancas")


@login_required
def cobranca_recibo(request, pk):
    c = get_object_or_404(Cobranca.objects.select_related("cliente"), pk=pk, status=Cobranca.PAGA)
    return render(request, "cobranca/recibo.html", {"c": c, "config": Configuracao.atual()})


# ---------------------------------------------------------------- geração
@login_required
def gerar(request):
    hoje = timezone.localdate()
    competencia = _mes(request.POST.get("competencia") or request.GET.get("competencia"), hoje.replace(day=1))
    if request.method == "POST":
        ids = [int(i) for i in request.POST.getlist("contratos") if i.isdigit()]
        criadas = services.gerar_mensalidades(competencia, request.user, contratos_ids=set(ids))
        messages.success(request, f"{len(criadas)} cobrança(s) gerada(s) para {services.competencia_texto(competencia)}.")
        return redirect(f"/cobrancas/?situacao=abertas&competencia={competencia:%Y-%m}")
    itens = services.previa_mensalidades(competencia)
    novos = [i for i in itens if not i["ja_existe"]]
    return render(request, "cobranca/gerar.html", {
        "competencia": competencia, "itens": itens,
        "total_novo": sum((i["valor"] for i in novos), Decimal(0)), "qtd_novo": len(novos),
    })


# ---------------------------------------------------------------- inadimplência
@login_required
def inadimplencia(request):
    hoje = timezone.localdate()
    config = Configuracao.atual()
    qs = Cobranca.objects.filter(status=Cobranca.ABERTA, vencimento__lt=hoje).select_related("cliente", "cliente__responsavel")
    if request.GET.get("responsavel"):
        qs = qs.filter(cliente__responsavel_id=request.GET["responsavel"])
    grupos = defaultdict(list)
    for c in qs.order_by("vencimento"):
        grupos[c.cliente].append(c)
    linhas = []
    faixas = {"ate30": Decimal(0), "31a60": Decimal(0), "61a90": Decimal(0), "mais90": Decimal(0)}
    for cliente, cobs in grupos.items():
        maior_atraso = max(c.dias_atraso(hoje) for c in cobs)
        for c in cobs:
            d = c.dias_atraso(hoje)
            faixa = "ate30" if d <= 30 else "31a60" if d <= 60 else "61a90" if d <= 90 else "mais90"
            faixas[faixa] += c.valor
        linhas.append({
            "cliente": cliente, "cobrancas": cobs, "qtd": len(cobs), "maior_atraso": maior_atraso,
            "total": sum((c.valor for c in cobs), Decimal(0)),
            "atualizado": sum((c.valor_atualizado(hoje, config) for c in cobs), Decimal(0)),
            "whatsapp": services.link_whatsapp(cobs[0], config),
        })
    linhas.sort(key=lambda l: (-l["maior_atraso"], -l["total"]))
    return render(request, "cobranca/inadimplencia.html", {
        "linhas": linhas, "faixas": faixas,
        "total": sum((l["total"] for l in linhas), Decimal(0)),
        "total_atualizado": sum((l["atualizado"] for l in linhas), Decimal(0)),
        "usuarios": equipe(),
    })


# ---------------------------------------------------------------- reajustes
@login_required
def reajustes(request):
    hoje = timezone.localdate()
    mes_ref = _mes(request.GET.get("mes") or request.POST.get("mes"), hoje.replace(day=1))
    if request.method == "POST":
        aplicados = 0
        for cid in request.POST.getlist("contratos"):
            contrato = get_object_or_404(Contrato, pk=cid)
            bruto = request.POST.get(f"perc_{cid}", "").replace(",", ".").strip()
            try:
                perc = Decimal(bruto)
            except InvalidOperation:
                messages.error(request, f"{contrato.cliente}: percentual inválido.")
                continue
            services.aplicar_reajuste(
                contrato, perc, request.POST.get(f"periodo_{cid}", ""), request.user,
                atualizar_abertas=bool(request.POST.get("atualizar_abertas")),
            )
            aplicados += 1
        messages.success(request, f"Reajuste aplicado em {aplicados} contrato(s).")
        return redirect(f"/reajustes/?mes={mes_ref:%Y-%m}")

    hoje_ref = mes_ref if mes_ref != hoje.replace(day=1) else hoje
    contratos = [c for c in Contrato.objects.filter(ativo=True).select_related("cliente")
                 if c.reajuste_pendente(hoje_ref)]
    cache, erros = {}, []
    for c in contratos:
        if c.indice_reajuste == "FIXO":
            c.perc_sugerido, c.periodo = c.percentual_fixo, "percentual fixo"
            continue
        if c.indice_reajuste not in cache:
            try:
                cache[c.indice_reajuste] = services.indice_acumulado_12m(c.indice_reajuste, mes_ref)
            except services.IndiceIndisponivel as e:
                cache[c.indice_reajuste] = (None, "")
                erros.append(str(e))
        c.perc_sugerido, c.periodo = cache[c.indice_reajuste]
        if c.perc_sugerido is not None:
            c.valor_sugerido = (c.valor_mensal * (1 + c.perc_sugerido / 100)).quantize(Decimal("0.01"))
    for e in set(erros):
        messages.warning(request, e + " Digite o percentual manualmente.")
    return render(request, "cobranca/reajustes.html", {
        "contratos": contratos, "mes": mes_ref,
        "historico": HistoricoReajuste.objects.select_related("contrato__cliente", "usuario")[:30],
    })


# ---------------------------------------------------------------- configuração
@login_required
def configuracao(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Somente administradores.")
    form = ConfiguracaoForm(request.POST or None, instance=Configuracao.atual())
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Configurações salvas.")
        return redirect("configuracao")
    return render(request, "cobranca/form.html", {
        "form": form, "titulo": "Configurações do escritório",
        "extra": {
            "asaas": "configurado (" + settings.ASAAS_AMBIENTE + ")" if asaas.configurado() else "não configurado",
            "webhook": request.build_absolute_uri("/webhooks/asaas/"),
        },
    })


# ---------------------------------------------------------------- webhook
@csrf_exempt
@require_POST
def webhook_asaas(request):
    token = request.headers.get("asaas-access-token", "")
    if not settings.ASAAS_WEBHOOK_TOKEN or not secrets.compare_digest(token, settings.ASAAS_WEBHOOK_TOKEN):
        return HttpResponseForbidden("token inválido")
    try:
        evento = json.loads(request.body)
    except ValueError:
        return JsonResponse({"erro": "json inválido"}, status=400)
    resultado = asaas.processar_webhook(evento)
    log.info("Webhook Asaas %s: %s", evento.get("event"), resultado)
    return JsonResponse({"ok": True, "resultado": resultado})


# ---------------------------------------------------------------- completar cadastros em lote
@login_required
def completar_lote(request):
    """Grade para completar o que a importação não traz: regime, responsável, funcionários, honorário."""
    from obrigacoes.services import aplicar_padrao_regime
    qs = Cliente.objects.filter(ativo=True).select_related("responsavel").prefetch_related("contratos", "obrigacoes")
    filtro = request.GET.get("filtro", "incompletos")
    if request.method == "POST":
        alterados = obrig = contratos = 0
        aplicar = bool(request.POST.get("aplicar_obrigacoes"))
        usuarios = {str(u.pk): u for u in equipe()}
        for c in qs.filter(pk__in=[int(i) for i in request.POST.getlist("ids") if i.isdigit()]):
            antes = (c.regime, c.responsavel_id, c.tem_funcionarios)
            regime = request.POST.get(f"regime_{c.pk}", c.regime)
            if regime in dict(Cliente.REGIMES):
                c.regime = regime
            c.responsavel = usuarios.get(request.POST.get(f"resp_{c.pk}", ""), None)
            c.tem_funcionarios = bool(request.POST.get(f"func_{c.pk}"))
            if (c.regime, c.responsavel_id, c.tem_funcionarios) != antes:
                c.save()
                alterados += 1
            if aplicar and c.regime != "OUTRO" and (antes[0] != c.regime or antes[2] != c.tem_funcionarios or not c.obrigacoes.exists()):
                obrig += aplicar_padrao_regime(c)
            valor = request.POST.get(f"valor_{c.pk}", "").strip()
            if valor and not c.contratos.exists():
                try:
                    v = Decimal(valor.replace(".", "").replace(",", ".")) if "," in valor else Decimal(valor)
                    dia = int(request.POST.get(f"dia_{c.pk}") or 10)
                    if v > 0 and 1 <= dia <= 31:
                        Contrato.objects.create(cliente=c, valor_mensal=v, dia_vencimento=dia,
                                                data_inicio=timezone.localdate().replace(day=1),
                                                mes_reajuste=timezone.localdate().month)
                        contratos += 1
                except (InvalidOperation, ValueError):
                    messages.error(request, f"{c}: valor de honorário inválido.")
        messages.success(request, f"{alterados} cliente(s) atualizado(s), {contratos} honorário(s) criado(s), "
                                  f"{obrig} obrigação(ões) vinculada(s).")
        return redirect(request.get_full_path())
    if filtro == "incompletos":
        qs = qs.filter(Q(regime="OUTRO") | Q(responsavel__isnull=True) | Q(contratos__isnull=True)).distinct()
    return render(request, "cobranca/completar_lote.html", {
        "clientes": qs.order_by("nome"), "filtro": filtro, "regimes": Cliente.REGIMES, "usuarios": equipe(),
        "qtd_outro": Cliente.objects.filter(ativo=True, regime="OUTRO").count(),
    })
