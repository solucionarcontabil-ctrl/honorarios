import io
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from cobranca.models import Cliente

from . import services
from .models import Documento, Lote

LIMITE_ENVIO = 300 * 1024 * 1024  # 300 MB por envio


def _mes(valor, padrao=None):
    try:
        ano, mes = (valor or "").split("-")
        return date(int(ano), int(mes), 1)
    except ValueError:
        if padrao:
            return padrao
        hoje = timezone.localdate()
        return date(hoje.year - (hoje.month == 1), (hoje.month - 2) % 12 + 1, 1)  # mês anterior


@login_required
def central(request):
    comp = _mes(request.GET.get("competencia"))
    if request.method == "POST":
        arquivos = request.FILES.getlist("arquivos")
        if not arquivos:
            messages.error(request, "Selecione ao menos um arquivo .xml ou .zip.")
            return redirect("xml_central")
        if sum(f.size for f in arquivos) > LIMITE_ENVIO:
            messages.error(request, "Envio maior que 300 MB. Divida em partes.")
            return redirect("xml_central")
        lote = services.importar([(f.name, f.read()) for f in arquivos], request.user)
        nivel = messages.success if not lote.erros else messages.warning
        nivel(request, f"{lote.arquivos} arquivo(s) lido(s): {lote.novos} nota(s) nova(s), {lote.eventos} evento(s), "
                       f"{lote.repetidos} repetido(s), {lote.sem_cliente} sem cliente, {lote.erros} com erro.")
        return redirect("xml_lote", pk=lote.pk)

    base = Documento.objects.filter(competencia=comp)
    por_cliente = {}
    for campo, chave in (("cliente_emitente", "emit"), ("cliente_destinatario", "dest")):
        for r in (base.filter(**{f"{campo}__isnull": False}).values(campo)
                  .annotate(qtd=Count("id"), total=Sum("valor_total", filter=Q(situacao="AUTORIZADA")),
                            canc=Count("id", filter=Q(situacao="CANCELADA"))).order_by()):
            linha = por_cliente.setdefault(r[campo], {})
            linha[chave] = r
    clientes = {c.pk: c for c in Cliente.objects.filter(pk__in=por_cliente)}
    linhas = sorted(
        ({"cliente": clientes[cid], **v} for cid, v in por_cliente.items() if cid in clientes),
        key=lambda l: str(l["cliente"]).lower(),
    )
    ativos_sem_xml = Cliente.objects.filter(ativo=True).exclude(pk__in=por_cliente).exclude(regime="PF")
    return render(request, "xmls/central.html", {
        "competencia": comp, "linhas": linhas, "sem_xml": ativos_sem_xml,
        "qtd_sem_cliente": Documento.objects.filter(cliente_emitente__isnull=True, cliente_destinatario__isnull=True).count(),
        "lotes": Lote.objects.select_related("enviado_por")[:8],
        "total_docs": Documento.objects.count(),
    })


@login_required
def lote(request, pk):
    lt = get_object_or_404(Lote, pk=pk)
    return render(request, "xmls/lote.html", {
        "lote": lt, "erros": lt.detalhes_erros.splitlines(),
        "docs": lt.documentos.select_related("cliente_emitente", "cliente_destinatario")[:300],
    })


def _filtrar(request, cliente, comp):
    aba = request.GET.get("aba", "emitidas")
    qs = services.emitidas(cliente, comp) if aba == "emitidas" else services.recebidas(cliente, comp)
    if request.GET.get("modelo"):
        qs = qs.filter(modelo=request.GET["modelo"])
    if request.GET.get("situacao"):
        qs = qs.filter(situacao=request.GET["situacao"])
    return qs.order_by("emissao", "numero"), aba


@login_required
def cliente_xmls(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    comp = _mes(request.GET.get("competencia"))
    qs, aba = _filtrar(request, cliente, comp)
    formato = request.GET.get("baixar")
    if formato == "zip":
        resp = HttpResponse(services.zip_xmls(qs), content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="xml-{cliente.documento}-{comp:%Y-%m}-{aba}.zip"'
        return resp
    if formato == "excel":
        return _excel(cliente, comp, aba, qs)
    return render(request, "xmls/cliente.html", {
        "cliente": cliente, "competencia": comp, "aba": aba, "docs": qs[:1000], "qtd": qs.count(),
        "somas": services.somar(qs), "cfops": services.resumo_cfop(qs),
        "qtd_emitidas": services.emitidas(cliente, comp).count(),
        "qtd_recebidas": services.recebidas(cliente, comp).count(),
        "filtros": request.GET, "query": request.GET.urlencode(),
        "modelos": Documento.MODELOS, "situacoes": Documento.SITUACOES,
    })


def _excel(cliente, comp, aba, qs):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = aba.capitalize()
    cab = ["Modelo", "Número", "Série", "Emissão", "Chave", "Emitente", "CNPJ/CPF emitente", "Destinatário/Tomador",
           "CNPJ/CPF dest.", "Operação", "Natureza", "CFOPs", "Situação", "Valor total", "ICMS", "ICMS ST", "IPI",
           "PIS", "COFINS", "ISS"]
    ws.append(cab)
    for c in ws[1]:
        c.font = Font(bold=True)
    for d in qs:
        ws.append([d.get_modelo_display(), d.numero, d.serie, d.emissao, d.chave, d.emit_nome, d.emit_doc, d.dest_nome,
                   d.dest_doc, d.get_tipo_operacao_display(), d.natureza, d.lista_cfops, d.get_situacao_display(),
                   float(d.valor_total), float(d.valor_icms), float(d.valor_icms_st), float(d.valor_ipi),
                   float(d.valor_pis), float(d.valor_cofins), float(d.valor_iss)])
    for col in "NOPQRST":
        for cel in ws[col][1:]:
            cel.number_format = "#,##0.00"
    for cel in ws["D"][1:]:
        cel.number_format = "DD/MM/YYYY"
    ws2 = wb.create_sheet("Por CFOP")
    ws2.append(["CFOP", "Valor (autorizadas)"])
    for cfop, valor in services.resumo_cfop(qs):
        ws2.append([cfop, float(valor)])
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="notas-{cliente.documento}-{comp:%Y-%m}-{aba}.xlsx"'
    return resp


@login_required
def faturamento(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    ate = _mes(request.GET.get("ate"), timezone.localdate().replace(day=1))
    linhas, ano = services.faturamento_12m(cliente, ate)
    rbt12 = linhas[-1]["rbt12"] if linhas else 0
    alertas = []
    if cliente.regime == "MEI" and ano > services.LIMITE_MEI * 80 / 100:
        alertas.append(f"Faturamento de {ate.year} já passa de 80% do limite do MEI.")
    if cliente.regime == "SIMPLES":
        if rbt12 > services.LIMITE_SIMPLES:
            alertas.append("RBT12 acima do limite do Simples Nacional.")
        elif rbt12 > services.SUBLIMITE_ICMS_ISS:
            alertas.append("RBT12 acima do sublimite de ICMS/ISS.")
        elif rbt12 > services.SUBLIMITE_ICMS_ISS * 85 / 100:
            alertas.append("RBT12 se aproximando do sublimite de ICMS/ISS (acima de 85%).")
    maior = max([l["valor"] for l in linhas] + [1])
    for l in linhas:
        l["perc"] = int(l["valor"] / maior * 100)
    return render(request, "xmls/faturamento.html", {
        "cliente": cliente, "ate": ate, "linhas": linhas, "ano": ano, "rbt12": rbt12, "alertas": alertas,
        "limites": {"simples": services.LIMITE_SIMPLES, "sublimite": services.SUBLIMITE_ICMS_ISS, "mei": services.LIMITE_MEI},
    })


@login_required
def documento(request, pk):
    d = get_object_or_404(Documento, pk=pk)
    if request.GET.get("baixar"):
        resp = HttpResponse(d.xml, content_type="application/xml")
        resp["Content-Disposition"] = f'attachment; filename="{d.chave}.xml"'
        return resp
    from .models import Evento
    return render(request, "xmls/documento.html", {"d": d, "eventos": Evento.objects.filter(chave=d.chave)})


@login_required
def sem_cliente(request):
    qs = Documento.objects.filter(cliente_emitente__isnull=True, cliente_destinatario__isnull=True)
    emit = qs.values("emit_doc", "emit_nome").annotate(qtd=Count("id"), total=Sum("valor_total")).order_by("-qtd")[:100]
    dest = (qs.exclude(dest_doc="").values("dest_doc", "dest_nome").annotate(qtd=Count("id"), total=Sum("valor_total"))
            .order_by("-qtd")[:100])
    return render(request, "xmls/sem_cliente.html", {"emit": emit, "dest": dest, "qtd": qs.count()})


@login_required
@require_POST
def revincular(request):
    n = services.revincular()
    messages.success(request, f"{n} nota(s) vinculada(s) a clientes.")
    return redirect("xml_sem_cliente")
