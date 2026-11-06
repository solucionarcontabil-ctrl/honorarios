import json
from datetime import date
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db.models import Count, Max, Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from cobranca.forms import DATA, BootstrapMixin
from cobranca.models import Cliente
from cobranca.validators import cpf_valido, so_digitos
from contabil.views import valor_br
from portal.services import ip_de

from . import pdf, services
from .models import ConfigFolha, Dependente, Empregado, Ferias, Folha, Rescisao, TabelaLegal, Variavel


# ------------------------------------------------------------------ formulários
class EmpregadoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Empregado
        exclude = ["cliente", "matricula", "criado_em", "demissao", "ativo"]
        widgets = {"nascimento": DATA, "admissao": DATA, "fim_contrato": DATA, "observacoes": forms.Textarea(attrs={"rows": 2})}

    def clean_cpf(self):
        cpf = so_digitos(self.cleaned_data["cpf"])
        if not cpf_valido(cpf):
            raise forms.ValidationError("CPF inválido.")
        return cpf

    def clean(self):
        d = super().clean()
        if d.get("contrato") in ("EXPERIENCIA", "DETERMINADO") and not d.get("fim_contrato"):
            self.add_error("fim_contrato", "Informe o fim do contrato.")
        if d.get("contrato") == "EXPERIENCIA" and d.get("fim_contrato") and d.get("admissao") \
                and (d["fim_contrato"] - d["admissao"]).days > 89:
            self.add_error("fim_contrato", "Contrato de experiência tem no máximo 90 dias (art. 445 da CLT).")
        return d


class DependenteForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Dependente
        exclude = ["empregado"]
        widgets = {"nascimento": DATA}


class FeriasForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Ferias
        fields = ["empregado", "aquisitivo_inicio", "aquisitivo_fim", "gozo_inicio", "dias", "abono_dias", "adiantar_13", "medias"]
        widgets = {"aquisitivo_inicio": DATA, "aquisitivo_fim": DATA, "gozo_inicio": DATA}


class RescisaoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Rescisao
        fields = ["tipo", "data_aviso", "data_desligamento", "aviso", "saldo_fgts", "ferias_vencidas_dias", "medias", "outros_descontos"]
        widgets = {"data_aviso": DATA, "data_desligamento": DATA}


class ConfigForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ConfigFolha
        exclude = ["cliente"]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 2})}


def _mes(valor):
    try:
        a, m = (valor or "").split("-")[:2]
        return date(int(a), int(m), 1)
    except ValueError:
        return timezone.localdate().replace(day=1)


# ------------------------------------------------------------------ visão geral
@login_required
def inicio(request):
    clientes = (Cliente.objects.filter(ativo=True).exclude(regime="PF")
                .annotate(qtd=Count("empregados", filter=Q(empregados__ativo=True)), ultima=Max("folhas__competencia")))
    return render(request, "folha/inicio.html", {"clientes": clientes,
                                                 "pendentes": TabelaLegal.objects.filter(conferida=False).count()})


@login_required
def cliente(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    mostrar = request.GET.get("todos")
    emps = c.empregados.all() if mostrar else c.empregados.filter(ativo=True)
    hoje = timezone.localdate()
    return render(request, "folha/cliente.html", {
        "c": c, "empregados": emps.prefetch_related("dependentes"), "folhas": c.folhas.all()[:24],
        "cfg": services.config(c), "competencia": hoje.replace(day=1),
        "ferias": Ferias.objects.filter(empregado__cliente=c).select_related("empregado")[:12],
        "tipos": Folha.TIPOS, "mostrar_todos": mostrar,
    })


@login_required
def config(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    form = ConfigForm(request.POST or None, instance=services.config(c))
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Configuração da folha salva.")
        return redirect("folha_cliente", cid=c.pk)
    return render(request, "cobranca/form.html", {"form": form, "titulo": f"Configuração da folha — {c}",
                                                  "voltar": f"/folha/{c.pk}/"})


@login_required
def empregado(request, cid, pk=None):
    c = get_object_or_404(Cliente, pk=cid)
    e = get_object_or_404(Empregado, pk=pk, cliente=c) if pk else None
    form = EmpregadoForm(request.POST or None, instance=e)
    dep_form = DependenteForm(request.POST if request.POST.get("parte") == "dependente" else None, prefix="dep")
    if request.method == "POST":
        if request.POST.get("parte") == "dependente" and e:
            if dep_form.is_valid():
                d = dep_form.save(commit=False)
                d.empregado = e
                d.save()
                messages.success(request, "Dependente incluído.")
                return redirect("folha_empregado", cid=c.pk, pk=e.pk)
        elif request.POST.get("parte") == "excluir_dep" and e:
            e.dependentes.filter(pk=request.POST.get("dep")).delete()
            return redirect("folha_empregado", cid=c.pk, pk=e.pk)
        elif form.is_valid():
            obj = form.save(commit=False)
            obj.cliente = c
            if not obj.matricula:
                obj.matricula = services.proxima_matricula(c)
            obj.save()
            messages.success(request, "Cadastro salvo.")
            return redirect("folha_empregado", cid=c.pk, pk=obj.pk)
    return render(request, "folha/empregado.html", {"c": c, "e": e, "form": form, "dep_form": dep_form})


# ------------------------------------------------------------------ variáveis do mês
TIPOS_GRADE = ["HE50", "HE100", "NOTURNO", "FALTAS", "DSR_PERDIDO", "ATRASOS", "AFASTAMENTO", "COMISSAO",
               "GRATIFICACAO", "PREMIO", "OUTRO_PROVENTO", "DESC_PLANO", "DESC_OUTRO", "DESC_ADIANT"]


@login_required
def variaveis(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    comp = _mes(request.GET.get("competencia") or request.POST.get("competencia"))
    fim = date(comp.year, comp.month, 28)
    emps = [e for e in c.empregados.all() if e.ativo_em(comp, fim)]
    if request.method == "POST":
        for e in emps:
            for t in TIPOS_GRADE:
                bruto = request.POST.get(f"v_{e.pk}_{t}", "")
                v = valor_br(bruto)
                Variavel.objects.filter(empregado=e, competencia=comp, tipo=t).delete()
                if v:
                    Variavel.objects.create(empregado=e, competencia=comp, tipo=t, quantidade=v)
        messages.success(request, "Variáveis salvas. Recalcule a folha para aplicar.")
        return redirect(f"/folha/{c.pk}/variaveis/?competencia={comp:%Y-%m}")
    atuais = {(v.empregado_id, v.tipo): v.quantidade for v in Variavel.objects.filter(empregado__cliente=c, competencia=comp)}
    nomes = dict(Variavel.TIPOS)
    linhas = [{"e": e, "campos": [(t, atuais.get((e.pk, t))) for t in TIPOS_GRADE]} for e in emps]
    return render(request, "folha/variaveis.html", {"c": c, "comp": comp, "linhas": linhas,
                                                    "cabecalho": [(t, nomes[t]) for t in TIPOS_GRADE]})


# ------------------------------------------------------------------ folhas
@login_required
@require_POST
def calcular(request, cid):
    c = get_object_or_404(Cliente, pk=cid)
    comp = _mes(request.POST.get("competencia"))
    tipo = request.POST.get("tipo", "MENSAL")
    if tipo == "DECIMO1":
        comp = date(comp.year, 11, 1)
    elif tipo == "DECIMO2":
        comp = date(comp.year, 12, 1)
    try:
        f = services.calcular_folha(c, comp, tipo, request.user)
        messages.success(request, f"{f} calculada: {f.holerites.count()} holerite(s).")
        return redirect("folha_detalhe", pk=f.pk)
    except (services.ErroFolha, LookupError) as e:
        messages.error(request, str(e))
        return redirect("folha_cliente", cid=c.pk)


@login_required
def detalhe(request, pk):
    f = get_object_or_404(Folha.objects.select_related("cliente"), pk=pk)
    return render(request, "folha/folha.html", {"f": f, "c": f.cliente,
                                                "holerites": f.holerites.select_related("empregado")})


@login_required
@require_POST
def acao(request, pk, acao):
    f = get_object_or_404(Folha, pk=pk)
    try:
        if acao == "recalcular":
            services.calcular_folha(f.cliente, f.competencia, f.tipo, request.user)
            messages.success(request, "Folha recalculada.")
        elif acao == "fechar":
            services.fechar(f, request.user)
            messages.success(request, "Folha fechada. A folha do mês foi enviada para o fator R do fiscal.")
        elif acao == "reabrir":
            services.reabrir(f, request.user)
            messages.warning(request, "Folha reaberta.")
        elif acao == "contabilizar":
            lanc = services.contabilizar(f, request.user, ip_de(request))
            messages.success(request, f"Lançamento contábil nº {lanc.numero} gerado.")
        elif acao == "portal":
            if not f.fechada:
                raise services.ErroFolha("Feche a folha antes de publicar os holerites.")
            from portal.services import publicar
            arquivo = ContentFile(pdf.holerites_pdf(list(f.holerites.select_related("empregado", "folha__cliente"))),
                                  name=f"holerites-{f.competencia:%Y-%m}-{f.tipo.lower()}.pdf")
            prot, _ = publicar(f.cliente, [arquivo], request.user, "FOLHA", f"{f.get_tipo_display()} {f.competencia:%m/%Y} — holerites",
                               f.competencia)
            messages.success(request, f"Holerites publicados no portal do cliente (protocolo {prot.codigo}).")
    except (services.ErroFolha, LookupError) as e:
        messages.error(request, str(e))
    return redirect("folha_detalhe", pk=f.pk)


@login_required
def holerites_pdf(request, pk):
    f = get_object_or_404(Folha, pk=pk)
    hs = f.holerites.select_related("empregado", "folha__cliente")
    if request.GET.get("empregado"):
        hs = hs.filter(empregado_id=request.GET["empregado"])
    resp = HttpResponse(pdf.holerites_pdf(list(hs)), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="holerites-{f.competencia:%Y-%m}.pdf"'
    return resp


# ------------------------------------------------------------------ férias
@login_required
def ferias(request, cid, pk=None):
    c = get_object_or_404(Cliente, pk=cid)
    f = get_object_or_404(Ferias, pk=pk, empregado__cliente=c) if pk else None
    inicial = {}
    if not f and request.GET.get("empregado"):
        e = c.empregados.filter(pk=request.GET["empregado"]).first()
        if e:
            from . import calculo as C
            completos, atual = C.periodos_aquisitivos(e.admissao, timezone.localdate())
            gozados = set(e.ferias.values_list("aquisitivo_inicio", flat=True))
            pend = [p for p in completos if p[0] not in gozados]
            if pend:
                inicial = {"empregado": e.pk, "aquisitivo_inicio": pend[0][0], "aquisitivo_fim": pend[0][1]}
            else:
                inicial = {"empregado": e.pk}
    form = FeriasForm(request.POST or None, instance=f, initial=inicial)
    form.fields["empregado"].queryset = c.empregados.filter(ativo=True).exclude(tipo="PROLABORE")
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        try:
            services.calcular_ferias(obj, request.user)
            messages.success(request, "Férias calculadas. Pagamento até " + obj.pagamento_ate.strftime("%d/%m/%Y") + ".")
            return redirect("folha_ferias", cid=c.pk, pk=obj.pk)
        except (services.ErroFolha, LookupError) as e:
            messages.error(request, str(e))
    return render(request, "folha/ferias.html", {"c": c, "f": f, "form": form})


# ------------------------------------------------------------------ rescisão
@login_required
def rescisao(request, cid, emp_id):
    c = get_object_or_404(Cliente, pk=cid)
    e = get_object_or_404(Empregado, pk=emp_id, cliente=c)
    try:
        r = e.rescisao
    except Rescisao.DoesNotExist:
        r = None
    form = RescisaoForm(request.POST or None, instance=r)
    if request.method == "POST":
        if request.POST.get("acao") == "confirmar" and r and r.valores:
            e.demissao, e.ativo = r.data_desligamento, False
            e.save(update_fields=["demissao", "ativo"])
            messages.success(request, f"Desligamento de {e.nome} registrado. Lembre: eventos do eSocial e guia do FGTS rescisório.")
            return redirect("folha_cliente", cid=c.pk)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.empregado = e
            try:
                services.calcular_rescisao(obj, request.user)
                messages.success(request, "Rescisão calculada.")
                return redirect("folha_rescisao", cid=c.pk, emp_id=e.pk)
            except (services.ErroFolha, LookupError) as err:
                messages.error(request, str(err))
    return render(request, "folha/rescisao.html", {"c": c, "e": e, "r": r, "form": form})


# ------------------------------------------------------------------ tabelas legais
@login_required
def tabelas(request):
    if request.method == "POST":
        if not request.user.is_staff:
            return HttpResponseForbidden("Somente administradores.")
        t = get_object_or_404(TabelaLegal, pk=request.POST.get("id")) if request.POST.get("id") else TabelaLegal()
        if request.POST.get("acao") == "conferir":
            t.conferida, t.conferida_por, t.conferida_em = True, request.user, timezone.now()
            t.save()
            messages.success(request, f"{t} marcada como conferida.")
        else:
            try:
                t.tipo = request.POST.get("tipo") or t.tipo
                t.vigencia_inicio = date.fromisoformat(request.POST.get("vigencia_inicio"))
                t.dados = json.loads(request.POST.get("dados"))
                t.fonte = request.POST.get("fonte", "")
                t.conferida, t.conferida_por, t.conferida_em = False, None, None  # alterou: precisa conferir de novo
                t.save()
                messages.success(request, "Tabela salva. Confira-a para liberar o fechamento das folhas.")
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                messages.error(request, f"Dados inválidos: {e}")
        return redirect("folha_tabelas")
    lista = list(TabelaLegal.objects.select_related("conferida_por"))
    for t in lista:
        t.dados_json = json.dumps(t.dados, ensure_ascii=False, indent=1)
    return render(request, "folha/tabelas.html", {"tabelas": lista, "tipos": TabelaLegal.TIPOS})
