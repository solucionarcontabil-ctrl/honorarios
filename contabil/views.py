import io
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from cobranca.models import Cliente
from portal.services import ip_de

from . import plano_padrao, rotinas, services
from .models import GRUPOS_DRE, NATUREZAS, PAPEIS, Conta, HistoricoPadrao, ItemExtrato, Lancamento, LogContabil, RegraExtrato
from .services import ErroContabil, PartidaDados


# ------------------------------------------------------------------ utilitários
def _data(valor, padrao):
    try:
        return date.fromisoformat(valor) if valor else padrao
    except ValueError:
        return padrao


def _periodo(request):
    hoje = timezone.localdate()
    ini = _data(request.GET.get("de"), date(hoje.year, 1, 1))
    fim = _data(request.GET.get("ate"), hoje)
    return (ini, fim) if ini <= fim else (fim, ini)


def valor_br(texto):
    t = (texto or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _conta_por_texto(cliente, texto):
    """Aceita '12', '12 - 1.1.01.002 Bancos' ou o código '1.1.01.002'."""
    t = (texto or "").strip()
    if not t:
        return None
    primeiro = t.split(" ")[0].split("-")[0].strip()
    if primeiro.isdigit():
        return cliente.contas.filter(reduzido=int(primeiro)).first()
    return cliente.contas.filter(codigo=primeiro).first()


def _erros(request, e):
    for m in (e.messages if hasattr(e, "messages") else [str(e)]):
        messages.error(request, m)


def _ctx(cliente, **kw):
    return {"cliente": cliente, "fechado_ate": services.fechado_ate(cliente), **kw}


# ------------------------------------------------------------------ visão geral
@login_required
def inicio(request):
    clientes = (Cliente.objects.filter(ativo=True).exclude(regime__in=["PF"])
                .annotate(qtd_contas=Count("contas", distinct=True), ultimo=Max("lancamentos__data"))
                .select_related("fechamento_contabil"))
    return render(request, "contabil/inicio.html", {"clientes": clientes})


@login_required
def cliente(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    ini, fim = _periodo(request)
    qs = c.lancamentos.filter(data__gte=ini, data__lte=fim).prefetch_related("partidas__conta")
    if request.GET.get("q"):
        qs = qs.filter(historico__icontains=request.GET["q"])
    if request.GET.get("origem"):
        qs = qs.filter(origem=request.GET["origem"])
    conta = _conta_por_texto(c, request.GET.get("conta"))
    if conta:
        qs = qs.filter(partidas__conta=conta).distinct()
    return render(request, "contabil/cliente.html", _ctx(
        c, lancamentos=qs.order_by("-data", "-numero")[:300], de=ini, ate=fim, filtros=request.GET,
        origens=Lancamento.ORIGENS, contas=c.contas.filter(sintetica=False, ativa=True),
        pendentes_extrato=c.itens_extrato.filter(status=ItemExtrato.PENDENTE).count(),
    ))


# ------------------------------------------------------------------ plano de contas
@login_required
def plano(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    if request.method == "POST" and request.POST.get("acao") == "padrao":
        try:
            n = plano_padrao.aplicar(c)
            services.registrar(c, request.user, "plano_padrao", f"{n} contas", ip=ip_de(request))
            messages.success(request, f"Plano padrão aplicado ({n} contas). Revise e preencha o plano referencial.")
        except ValueError as e:
            messages.error(request, str(e))
        return redirect("contabil_plano", cid=c.pk)
    contas = c.contas.annotate(qtd=Count("partidas"))
    return render(request, "contabil/plano.html", _ctx(c, contas=contas))


@login_required
def conta_form(request, cid, pk=None):
    c = get_object_or_404(Cliente, pk=cid)
    conta = get_object_or_404(Conta, pk=pk, cliente=c) if pk else Conta(cliente=c)
    usada = bool(pk and conta.partidas.exists())
    if request.method == "POST":
        antes = {"codigo": conta.codigo, "nome": conta.nome, "natureza": conta.natureza, "sintetica": conta.sintetica} if pk else None
        conta.codigo = request.POST.get("codigo", "").strip() if not usada else conta.codigo
        conta.nome = request.POST.get("nome", "").strip()
        conta.natureza = request.POST.get("natureza") if not usada else conta.natureza
        conta.sintetica = bool(request.POST.get("sintetica")) if not usada else conta.sintetica
        conta.grupo_dre = request.POST.get("grupo_dre", "")
        conta.papel = request.POST.get("papel", "")
        conta.codigo_referencial = request.POST.get("codigo_referencial", "").strip()
        conta.ativa = bool(request.POST.get("ativa"))
        if not pk:
            conta.reduzido = (c.contas.aggregate(m=Max("reduzido"))["m"] or 0) + 1
        try:
            if not conta.codigo or not conta.nome:
                raise ErroContabil("Informe código e nome.")
            if c.contas.filter(codigo=conta.codigo).exclude(pk=conta.pk).exists():
                raise ErroContabil("Já existe conta com esse código.")
            conta.full_clean(exclude=["cliente"])
            conta.save()
            services.registrar(c, request.user, "alterar_conta" if pk else "incluir_conta", conta.codigo, antes,
                               {"codigo": conta.codigo, "nome": conta.nome, "natureza": conta.natureza,
                                "sintetica": conta.sintetica, "papel": conta.papel}, ip_de(request))
            messages.success(request, "Conta salva.")
            return redirect("contabil_plano", cid=c.pk)
        except Exception as e:  # ValidationError / ErroContabil
            _erros(request, e)
    return render(request, "contabil/conta_form.html", _ctx(c, conta=conta, usada=usada, naturezas=NATUREZAS,
                                                            grupos=GRUPOS_DRE, papeis=PAPEIS))


# ------------------------------------------------------------------ lançamentos
def _ler_partidas(request, cliente):
    contas, tipos = request.POST.getlist("p_conta"), request.POST.getlist("p_tipo")
    valores, compl = request.POST.getlist("p_valor"), request.POST.getlist("p_complemento")
    partidas, erros = [], []
    for i, txt in enumerate(contas):
        v = valor_br(valores[i] if i < len(valores) else "")
        if not txt.strip() and not v:
            continue
        conta = _conta_por_texto(cliente, txt)
        if not conta:
            erros.append(f"Linha {i + 1}: conta '{txt}' não encontrada.")
            continue
        if v is None:
            erros.append(f"Linha {i + 1}: valor inválido.")
            continue
        partidas.append(PartidaDados(conta, tipos[i] if i < len(tipos) and tipos[i] in "DC" else "D", v,
                                     compl[i] if i < len(compl) else ""))
    return partidas, erros


@login_required
def lancamento_form(request, cid, pk=None):
    c = get_object_or_404(Cliente, pk=cid)
    lanc = get_object_or_404(Lancamento, pk=pk, cliente=c) if pk else None
    bloqueado = ""
    if lanc:
        fa = services.fechado_ate(c)
        if fa and lanc.data <= fa:
            bloqueado = f"Período fechado até {fa:%d/%m/%Y}: este lançamento só pode ser estornado."
        elif lanc.origem in ("ENCERRAMENTO", "ESTORNO"):
            bloqueado = "Lançamentos de encerramento e de estorno não são alterados."
    if request.method == "POST" and not bloqueado:
        partidas, erros = _ler_partidas(request, c)
        data_l = _data(request.POST.get("data"), None)
        if not data_l:
            erros.append("Data inválida.")
        historico = request.POST.get("historico", "").strip()
        if not historico:
            erros.append("Informe o histórico.")
        if not erros:
            try:
                if lanc:
                    services.alterar(lanc, data_l, historico, partidas, request.user, request.POST.get("documento", ""), ip_de(request))
                    messages.success(request, f"Lançamento {lanc.numero} alterado.")
                else:
                    lanc = services.lancar(c, data_l, historico, partidas, request.user, request.POST.get("documento", ""),
                                           ip=ip_de(request))
                    messages.success(request, f"Lançamento {lanc.numero} incluído.")
                    if request.POST.get("continuar"):
                        return redirect(f"/contabil/{c.pk}/lancamentos/novo/?data={data_l:%Y-%m-%d}")
                return redirect(f"/contabil/{c.pk}/?de={data_l:%Y-%m-01}&ate={data_l:%Y-%m-%d}")
            except ErroContabil as e:
                _erros(request, e)
        else:
            for e in erros:
                messages.error(request, e)
    linhas = []
    if request.method == "POST":
        for i, txt in enumerate(request.POST.getlist("p_conta")):
            linhas.append({"conta": txt, "tipo": request.POST.getlist("p_tipo")[i],
                           "valor": request.POST.getlist("p_valor")[i], "compl": request.POST.getlist("p_complemento")[i]})
    elif lanc:
        linhas = [{"conta": f"{p.conta.reduzido} - {p.conta.codigo} {p.conta.nome}", "tipo": p.tipo,
                   "valor": f"{p.valor:.2f}".replace(".", ","), "compl": p.complemento} for p in lanc.partidas.select_related("conta")]
    while len(linhas) < 4:
        linhas.append({"conta": "", "tipo": "D" if len(linhas) % 2 == 0 else "C", "valor": "", "compl": ""})
    return render(request, "contabil/lancamento_form.html", _ctx(
        c, lanc=lanc, linhas=linhas, bloqueado=bloqueado,
        contas=c.contas.filter(sintetica=False, ativa=True),
        historicos=HistoricoPadrao.objects.filter(cliente__in=[c]) | HistoricoPadrao.objects.filter(cliente__isnull=True),
        data_inicial=request.POST.get("data") or (lanc.data.isoformat() if lanc else request.GET.get("data") or timezone.localdate().isoformat()),
        historico=request.POST.get("historico") or (lanc.historico if lanc else ""),
        documento=request.POST.get("documento") or (lanc.documento if lanc else ""),
    ))


@login_required
@require_POST
def lancamento_acao(request, cid, pk, acao):
    c = get_object_or_404(Cliente, pk=cid)
    lanc = get_object_or_404(Lancamento, pk=pk, cliente=c)
    try:
        if acao == "excluir":
            services.excluir(lanc, request.user, ip_de(request))
            messages.success(request, "Lançamento excluído (registrado na auditoria).")
        elif acao == "estornar":
            data_e = _data(request.POST.get("data"), timezone.localdate())
            est = services.estornar(lanc, data_e, request.user, request.POST.get("motivo", ""), ip_de(request))
            messages.success(request, f"Estorno lançado (nº {est.numero}).")
    except ErroContabil as e:
        _erros(request, e)
    return redirect("contabil_cliente", cid=c.pk)


# ------------------------------------------------------------------ extrato bancário
@login_required
def extrato(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    bancos = c.contas.filter(sintetica=False, ativa=True, natureza="01", codigo__startswith="1.1.01")
    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "importar":
            arq = request.FILES.get("arquivo")
            conta_banco = bancos.filter(pk=request.POST.get("conta_banco") or 0).first()
            if not arq or not conta_banco:
                messages.error(request, "Escolha a conta bancária e o arquivo OFX.")
            elif arq.size > 10 * 1024 * 1024:
                messages.error(request, "Arquivo maior que 10 MB.")
            else:
                try:
                    novos, rep = rotinas.importar_ofx(c, conta_banco, arq.read())
                    messages.success(request, f"{novos} movimento(s) importado(s), {rep} já existiam.")
                except (rotinas.OFXInvalido, ErroContabil) as e:
                    _erros(request, e)
        elif acao in ("contabilizar", "ignorar", "salvar"):
            ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
            itens = list(c.itens_extrato.filter(pk__in=ids, status=ItemExtrato.PENDENTE))
            # grava as contrapartidas escolhidas e, se pedido, cria regra
            for it in c.itens_extrato.filter(status=ItemExtrato.PENDENTE):
                txt = request.POST.get(f"cp_{it.pk}")
                if txt is not None:
                    conta = _conta_por_texto(c, txt)
                    if (conta and not conta.sintetica) or not txt.strip():
                        it.contrapartida = conta
                        it.save(update_fields=["contrapartida"])
                    regra_txt = request.POST.get(f"regra_{it.pk}", "").strip()
                    if conta and regra_txt:
                        RegraExtrato.objects.get_or_create(cliente=c, texto=regra_txt[:100], conta=conta,
                                                           sentido="ENTRADA" if it.valor > 0 else "SAIDA")
            itens = list(c.itens_extrato.filter(pk__in=ids, status=ItemExtrato.PENDENTE).select_related("contrapartida", "conta_banco"))
            if acao == "contabilizar":
                feitos, erros = rotinas.contabilizar_itens(c, itens, request.user, ip_de(request))
                messages.success(request, f"{feitos} movimento(s) contabilizado(s).")
                sem = len([i for i in itens if not i.contrapartida])
                if sem:
                    messages.warning(request, f"{sem} selecionado(s) sem contrapartida não foram lançados.")
                for e in erros[:10]:
                    messages.error(request, e)
            elif acao == "ignorar":
                n = c.itens_extrato.filter(pk__in=ids, status=ItemExtrato.PENDENTE).update(status=ItemExtrato.IGNORADO)
                messages.info(request, f"{n} movimento(s) ignorado(s).")
            else:
                messages.success(request, "Contrapartidas salvas.")
        return redirect("contabil_extrato", cid=c.pk)
    status = request.GET.get("status", "PENDENTE")
    itens = c.itens_extrato.select_related("conta_banco", "contrapartida", "lancamento")
    if status != "TODOS":
        itens = itens.filter(status=status)
    return render(request, "contabil/extrato.html", _ctx(
        c, bancos=bancos, itens=itens[:500], status=status, contas=c.contas.filter(sintetica=False, ativa=True),
        regras=c.regras_extrato.select_related("conta")[:100]))


@login_required
@require_POST
def regra_excluir(request, cid, pk):
    get_object_or_404(RegraExtrato, pk=pk, cliente_id=cid).delete()
    messages.success(request, "Regra excluída.")
    return redirect("contabil_extrato", cid=cid)


# ------------------------------------------------------------------ notas fiscais
@login_required
def notas(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    hoje = timezone.localdate()
    comp = _data((request.GET.get("competencia") or request.POST.get("competencia") or "") + "-01", None) \
        or date(hoje.year - (hoje.month == 1), (hoje.month - 2) % 12 + 1, 1)
    if request.method == "POST":
        try:
            feitos, erros = rotinas.contabilizar_notas(c, comp, request.user, ip_de(request),
                                                       controla_estoque=bool(request.POST.get("estoque")))
            messages.success(request, f"{feitos} nota(s) contabilizada(s).")
            for e in erros[:10]:
                messages.error(request, e)
        except ErroContabil as e:
            _erros(request, e)
        return redirect(f"/contabil/{c.pk}/notas/?competencia={comp:%Y-%m}")
    itens = rotinas.previa_notas(c, comp)
    return render(request, "contabil/notas.html", _ctx(
        c, itens=itens, competencia=comp, qtd_novas=len([i for i in itens if not i["ja"]]),
        total_saidas=sum((i["valor"] for i in itens if i["tipo"] == "SAIDA"), Decimal(0)),
        total_entradas=sum((i["valor"] for i in itens if i["tipo"] == "ENTRADA"), Decimal(0))))


# ------------------------------------------------------------------ relatórios
@login_required
def balancete(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    ini, fim = _periodo(request)
    nivel = int(request.GET.get("nivel") or 9)
    linhas, td, tc = services.balancete(c, ini, fim)
    linhas = [l for l in linhas if l["conta"].nivel <= nivel]
    if request.GET.get("excel"):
        return _excel_balancete(c, ini, fim, linhas, td, tc)
    return render(request, "contabil/balancete.html", _ctx(c, linhas=linhas, td=td, tc=tc, de=ini, ate=fim, nivel=nivel,
                                                           fechado=td == tc, query=request.GET.urlencode()))


def _excel_balancete(c, ini, fim, linhas, td, tc):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Balancete"
    ws.append([f"Balancete de verificação — {c.nome} — CNPJ/CPF {c.documento_formatado}"])
    ws.append([f"Período: {ini:%d/%m/%Y} a {fim:%d/%m/%Y}"])
    ws.append([])
    ws.append(["Código", "Reduzido", "Conta", "Saldo anterior", "Débitos", "Créditos", "Saldo atual"])
    for cel in ws[4]:
        cel.font = Font(bold=True)
    for l in linhas:
        ws.append([l["conta"].codigo, l["conta"].reduzido, l["conta"].nome, float(l["saldo_ant"]), float(l["deb"]),
                   float(l["cred"]), float(l["saldo"])])
        if l["conta"].sintetica:
            for cel in ws[ws.max_row]:
                cel.font = Font(bold=True)
    ws.append(["", "", "TOTAIS (analíticas)", "", float(td), float(tc), ""])
    for col in "DEFG":
        for cel in ws[col][4:]:
            cel.number_format = '#,##0.00;[Red]-#,##0.00'
    ws.column_dimensions["C"].width = 45
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="balancete-{c.documento}-{fim:%Y-%m}.xlsx"'
    return resp


@login_required
def razao(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    ini, fim = _periodo(request)
    conta = _conta_por_texto(c, request.GET.get("conta"))
    dados = services.razao(conta, ini, fim) if conta else None
    return render(request, "contabil/razao.html", _ctx(c, conta=conta, dados=dados, de=ini, ate=fim,
                                                       contas=c.contas.filter(sintetica=False)))


@login_required
def diario(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    ini, fim = _periodo(request)
    return render(request, "contabil/diario.html", _ctx(c, lancamentos=services.diario(c, ini, fim)[:2000], de=ini, ate=fim))


@login_required
def dre(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    ini, fim = _periodo(request)
    return render(request, "contabil/dre.html", _ctx(c, d=services.dre(c, ini, fim), de=ini, ate=fim,
                                                     nomes=dict(GRUPOS_DRE)))


@login_required
def balanco(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    data_base = _data(request.GET.get("data"), timezone.localdate())
    return render(request, "contabil/balanco.html", _ctx(c, b=services.balanco(c, data_base), data_base=data_base))


# ------------------------------------------------------------------ fechamento e encerramento
@login_required
def fechamento(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    if request.method == "POST":
        acao = request.POST.get("acao")
        try:
            if acao == "fechar":
                ate = _data(request.POST.get("ate"), None)
                if not ate:
                    raise ErroContabil("Informe a data.")
                services.fechar_periodo(c, ate, request.user, ip_de(request))
                messages.success(request, f"Período fechado até {ate:%d/%m/%Y}.")
            elif acao == "reabrir":
                services.reabrir_periodo(c, _data(request.POST.get("ate"), None), request.user,
                                         request.POST.get("motivo", ""), ip_de(request))
                messages.warning(request, "Período reaberto. A reabertura ficou registrada na auditoria.")
            elif acao == "encerrar":
                ano = int(request.POST.get("ano"))
                _, _, resultado = services.encerrar_exercicio(c, ano, request.user, ip_de(request))
                messages.success(request, f"Exercício {ano} encerrado. Resultado: R$ {resultado:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        except (ErroContabil, ValueError, TypeError) as e:
            _erros(request, e)
        return redirect("contabil_fechamento", cid=c.pk)
    encerrados = c.lancamentos.filter(origem="ENCERRAMENTO", origem_ref__startswith="ARE-").values_list("data__year", flat=True)
    return render(request, "contabil/fechamento.html", _ctx(c, encerrados=sorted(set(encerrados)),
                                                            ano_sugerido=timezone.localdate().year - 1))


@login_required
def auditoria(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    return render(request, "contabil/auditoria.html", _ctx(c, logs=LogContabil.objects.filter(cliente=c).select_related("usuario")[:500]))
