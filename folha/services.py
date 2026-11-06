"""Processamento: folha mensal, adiantamento, 13º, férias, rescisão, encargos e integrações."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from . import calculo as C
from .calculo import ZERO, brl, r2
from .models import ConfigFolha, Empregado, Ferias, Folha, Holerite, TabelaLegal, Variavel

D = Decimal
CODIGOS_MEDIA = {"HE50", "HE100", "NOTURNO", "DSR", "COMISSAO"}  # entram nas médias de férias/13º/rescisão


class ErroFolha(Exception):
    pass


def config(cliente):
    cfg, criado = ConfigFolha.objects.get_or_create(cliente=cliente)
    if criado:
        cfg.aplicar_padrao_regime()
        cfg.save()
    return cfg


def proxima_matricula(cliente):
    return (Empregado.objects.filter(cliente=cliente).aggregate(m=Max("matricula"))["m"] or 0) + 1


def tabelas(data):
    t = {k: TabelaLegal.vigente(k, data) for k in ("INSS", "IRRF", "SALARIO_MINIMO", "SALARIO_FAMILIA")}
    avisos = []
    for k, tab in t.items():
        if not tab.conferida:
            avisos.append(f"A tabela {tab} ainda não foi conferida com a norma oficial. A folha não pode ser fechada.")
        if k in ("INSS", "SALARIO_MINIMO", "SALARIO_FAMILIA") and tab.vigencia_inicio.year < data.year:
            avisos.append(f"A tabela usada ({tab}) é de ano anterior. Cadastre a de {data.year} em Folha > Tabelas legais.")
    return t, avisos


class Itens:
    def __init__(self):
        self.lista = []

    def add(self, codigo, descricao, valor, tipo="P", ref="", inss=True, irrf=True, fgts=True, media=False):
        valor = r2(valor)
        if not valor:
            return
        self.lista.append({"codigo": codigo, "descricao": descricao, "referencia": str(ref), "tipo": tipo,
                           "valor": str(valor), "inss": inss, "irrf": irrf, "fgts": fgts, "media": media})

    def soma(self, tipo=None, campo=None):
        return sum((D(i["valor"]) for i in self.lista if (tipo is None or i["tipo"] == tipo) and (campo is None or i[campo])), ZERO)


def _dependentes(emp, data):
    ir = emp.dependentes.filter(ir=True).count()
    sf = sum(1 for d in emp.dependentes.filter(salario_familia=True) if d.invalido or d.idade_em(data) < 14)
    return ir, sf


def media_variaveis(emp, ate, meses=12):
    """Média mensal das variáveis (HE, adicional noturno, DSR, comissões) das folhas mensais dos últimos meses."""
    inicio = C.somar_meses_data(date(ate.year, ate.month, 1), -meses)
    total = ZERO
    for h in Holerite.objects.filter(empregado=emp, folha__tipo="MENSAL", folha__competencia__gte=inicio,
                                     folha__competencia__lt=date(ate.year, ate.month, 1)):
        total += sum((D(i["valor"]) for i in h.itens if i.get("media")), ZERO)
    return r2(total / meses)


def _ferias_no_mes(emp, competencia):
    """Parte das férias gozadas dentro da competência (bases de INSS/FGTS e INSS já descontado no recibo)."""
    ini_mes = competencia
    fim_mes = date(competencia.year, competencia.month, C.ultimo_dia(competencia.year, competencia.month))
    out = {"dias": 0, "base": ZERO, "inss": ZERO}
    for f in Ferias.objects.filter(empregado=emp, gozo_inicio__lte=fim_mes):
        if not f.valores or f.gozo_fim < ini_mes:
            continue
        a, b = max(f.gozo_inicio, ini_mes), min(f.gozo_fim, fim_mes)
        dias = (b - a).days + 1
        if dias <= 0:
            continue
        prop = D(dias) / D(f.dias)
        out["dias"] += dias
        out["base"] += r2(D(f.valores["base_inss"]) * prop)
        out["inss"] += r2(D(f.valores["inss"]) * prop)
    out["dias"] = min(out["dias"], 30)
    return out


# ------------------------------------------------------------------ folha mensal
def calcular_empregado(emp, competencia, cfg, tab, adiantado=ZERO):
    fim_mes = date(competencia.year, competencia.month, C.ultimo_dia(competencia.year, competencia.month))
    it, mem = Itens(), []
    var = defaultdict(lambda: ZERO)
    descricoes = {}
    for v in Variavel.objects.filter(empregado=emp, competencia=competencia):
        var[v.tipo] += v.quantidade
        if v.descricao:
            descricoes[v.tipo] = v.descricao
    dias_contrato = C.dias_mes_comercial(competencia, emp.admissao, emp.demissao)
    fer = _ferias_no_mes(emp, competencia) if not emp.prolabore else {"dias": 0, "base": ZERO, "inss": ZERO}
    afast = int(min(var["AFASTAMENTO"], dias_contrato))
    dias = max(dias_contrato - fer["dias"] - afast, 0)
    mem.append(f"Dias: {dias_contrato} no contrato − {fer['dias']} de férias − {afast} afastado = {dias}")
    sm = D(tab["SALARIO_MINIMO"].dados["valor"])
    inss_t, irrf_t = tab["INSS"].dados, tab["IRRF"].dados
    dep_ir, dep_sf = _dependentes(emp, fim_mes)

    if emp.prolabore:
        it.add("PROLABORE", "Pró-labore", emp.salario * dias / 30, ref=f"{dias}d", fgts=False)
        for tipo, cod in (("OUTRO_PROVENTO", "OUTRO"),):
            it.add(cod, descricoes.get(tipo, "Outros proventos"), var[tipo], fgts=False)
        base_inss = it.soma("P", "inss")
        inss = C.inss_contribuinte_individual(base_inss, inss_t)
        mem.append(f"INSS contribuinte individual: 11% de {brl(min(base_inss, D(inss_t['teto'])))} = {brl(inss)}")
    else:
        # base da hora: salário + insalubridade (OJ 47 SDI-1/TST) + periculosidade (Súmula 132/TST)
        adicionais = (sm * emp.insalubridade / 100 if emp.insalubridade else ZERO) + \
            (emp.salario * D("0.3") if emp.periculosidade else ZERO)
        hora = (emp.salario + adicionais) / emp.horas_mensais
        if adicionais and (var["HE50"] or var["HE100"] or var["NOTURNO"]):
            mem.append(f"Valor da hora: ({brl(emp.salario)} + adicionais {brl(adicionais)}) ÷ {emp.horas_mensais} = {brl(hora)}")
        it.add("SALARIO", "Salário", emp.salario * dias / 30, ref=f"{dias}d")
        it.add("HE50", "Horas extras 50%", hora * D("1.5") * var["HE50"], ref=f"{var['HE50']}h", media=True)
        it.add("HE100", "Horas extras 100%", hora * 2 * var["HE100"], ref=f"{var['HE100']}h", media=True)
        it.add("NOTURNO", "Adicional noturno 20%", hora * D("0.2") * var["NOTURNO"], ref=f"{var['NOTURNO']}h", media=True)
        it.add("COMISSAO", descricoes.get("COMISSAO", "Comissões"), var["COMISSAO"], media=True)
        variaveis_dsr = sum((D(i["valor"]) for i in it.lista if i["codigo"] in ("HE50", "HE100", "NOTURNO", "COMISSAO")), ZERO)
        if variaveis_dsr:
            uteis, domingos = C.dias_uteis_e_dsr(competencia)
            dsr = variaveis_dsr / uteis * domingos
            it.add("DSR", "DSR sobre variáveis", dsr, ref=f"{domingos}/{uteis}", media=True)
            mem.append(f"DSR: {brl(variaveis_dsr)} ÷ {uteis} dias úteis × {domingos} domingos/feriados = {brl(dsr)}")
        if emp.insalubridade:
            it.add("INSALUBRIDADE", f"Adicional de insalubridade {emp.insalubridade}%", sm * emp.insalubridade / 100 * dias / 30,
                   ref=f"{emp.insalubridade}% SM")
        if emp.periculosidade:
            it.add("PERICULOSIDADE", "Adicional de periculosidade 30%", emp.salario * D("0.3") * dias / 30, ref="30%")
        it.add("GRATIFICACAO", descricoes.get("GRATIFICACAO", "Gratificação"), var["GRATIFICACAO"])
        it.add("PREMIO", descricoes.get("PREMIO", "Prêmio"), var["PREMIO"], inss=False, fgts=False)
        it.add("OUTRO", descricoes.get("OUTRO_PROVENTO", "Outros proventos"), var["OUTRO_PROVENTO"])
        diaria = emp.salario / 30
        it.add("FALTAS", "Faltas", diaria * var["FALTAS"], tipo="D", ref=f"{var['FALTAS']}d")
        it.add("DSR_PERDIDO", "DSR perdido", diaria * var["DSR_PERDIDO"], tipo="D", ref=f"{var['DSR_PERDIDO']}d")
        it.add("ATRASOS", "Atrasos", hora * var["ATRASOS"], tipo="D", ref=f"{var['ATRASOS']}h")
        base_inss = it.soma("P", "inss") - it.soma("D", "inss")
        sf_tab = tab["SALARIO_FAMILIA"].dados
        if dep_sf and base_inss + fer["base"] <= D(sf_tab["limite"]):
            valor_sf = D(sf_tab["cota"]) * dep_sf * min(D(dias_contrato), D(30)) / 30
            it.add("SALFAMILIA", "Salário-família", valor_sf, ref=f"{dep_sf} cota(s)", inss=False, irrf=False, fgts=False)
        base_total = base_inss + fer["base"]
        inss_total, faixas = C.inss_empregado(base_total, inss_t)
        inss = max(inss_total - fer["inss"], ZERO)
        mem.append(f"INSS sobre {brl(base_total)}" + (f" (inclui {brl(fer['base'])} de férias)" if fer["base"] else "")
                   + f": {' + '.join(faixas)} = {brl(inss_total)}" + (f" − {brl(fer['inss'])} já descontado nas férias" if fer["inss"] else ""))
    it.add("INSS", "INSS", inss, tipo="D", ref="", inss=False, irrf=False, fgts=False)

    # IRRF (as férias têm tributação própria no recibo) e pensão (sobre o líquido depende do IRRF: iteração)
    rend_ir = it.soma("P", "irrf") - sum((D(i["valor"]) for i in it.lista if i["tipo"] == "D" and i["irrf"]), ZERO)
    pensao, ir, base_ir, mem_ir = ZERO, ZERO, ZERO, []
    for _ in range(6):
        ir, base_ir, mem_ir = C.irrf(rend_ir, inss, dep_ir, pensao, irrf_t)
        if emp.pensao_percentual:
            base_p = (it.soma("P") - it.soma("D")) if emp.pensao_base == "BRUTO" else (it.soma("P") - it.soma("D") - ir)
            pensao = r2(base_p * emp.pensao_percentual / 100)
    mem += mem_ir
    it.add("IRRF", "IRRF", ir, tipo="D", ref=f"{dep_ir} dep." if dep_ir else "", inss=False, irrf=False, fgts=False)
    it.add("PENSAO", "Pensão alimentícia", pensao, tipo="D", ref=f"{emp.pensao_percentual}%", inss=False, irrf=False, fgts=False)
    if emp.vt_custo_mensal and not emp.prolabore:
        vt = min(emp.salario * dias / 30 * D("0.06"), emp.vt_custo_mensal)
        it.add("VT", "Vale-transporte (6%)", vt, tipo="D", ref="6%", inss=False, irrf=False, fgts=False)
    it.add("PLANO", descricoes.get("DESC_PLANO", "Plano de saúde"), var["DESC_PLANO"], tipo="D", inss=False, irrf=False, fgts=False)
    it.add("DESC", descricoes.get("DESC_OUTRO", "Outros descontos"), var["DESC_OUTRO"], tipo="D", inss=False, irrf=False, fgts=False)
    it.add("ADIANT", "Adiantamento salarial", adiantado + var["DESC_ADIANT"], tipo="D", inss=False, irrf=False, fgts=False)

    proventos, descontos = it.soma("P"), it.soma("D")
    base_fgts = ZERO if emp.prolabore else (it.soma("P", "fgts") - sum((D(i["valor"]) for i in it.lista if i["tipo"] == "D" and i["fgts"]), ZERO) + fer["base"])
    fgts_aliq = D("2") if emp.tipo == "APRENDIZ" else cfg.fgts
    fgts = r2(base_fgts * fgts_aliq / 100)
    return {"itens": it.lista, "proventos": proventos, "descontos": descontos, "liquido": proventos - descontos,
            "base_inss": base_inss + fer["base"] if not emp.prolabore else base_inss, "inss": inss,
            "base_irrf": base_ir, "irrf": ir, "base_fgts": base_fgts, "fgts": fgts, "memoria": mem}


def _encargos(cfg, holerites):
    base_emp = sum((h.base_inss for h in holerites if not h.empregado.prolabore), ZERO)
    base_pro = sum((h.base_inss for h in holerites if h.empregado.prolabore), ZERO)
    cpp = r2((base_emp + base_pro) * cfg.cpp / 100)
    rat = r2(base_emp * cfg.rat * cfg.fap / 100)
    terceiros = r2(base_emp * cfg.terceiros / 100)
    inss_seg = sum((h.inss for h in holerites), ZERO)
    sal_fam = sum((D(i["valor"]) for h in holerites for i in h.itens if i["codigo"] == "SALFAMILIA"), ZERO)
    return {
        "base_empregados": str(base_emp), "base_prolabore": str(base_pro), "inss_segurados": str(inss_seg),
        "cpp": str(cpp), "rat": str(rat), "terceiros": str(terceiros), "salario_familia": str(sal_fam),
        "inss_total_a_recolher": str(inss_seg + cpp + rat + terceiros - sal_fam),
        "irrf": str(sum((h.irrf for h in holerites), ZERO)), "fgts": str(sum((h.fgts for h in holerites), ZERO)),
        "proventos": str(sum((h.proventos for h in holerites), ZERO)), "liquido": str(sum((h.liquido for h in holerites), ZERO)),
        "custo_total": str(sum((h.proventos for h in holerites), ZERO) + cpp + rat + terceiros + sum((h.fgts for h in holerites), ZERO)),
    }


@transaction.atomic
def calcular_folha(cliente, competencia, tipo="MENSAL", usuario=None, pagamento=None):
    folha, _ = Folha.objects.get_or_create(cliente=cliente, competencia=competencia, tipo=tipo)
    if folha.fechada:
        raise ErroFolha("Folha fechada. Reabra para recalcular.")
    cfg = config(cliente)
    ref = date(competencia.year, competencia.month, C.ultimo_dia(competencia.year, competencia.month))
    tab, avisos = tabelas(ref)
    folha.holerites.all().delete()
    empregados = [e for e in cliente.empregados.all() if e.ativo_em(competencia, ref)]
    criados = []
    for emp in empregados:
        if tipo == "MENSAL":
            adiant = ZERO
            fa = Holerite.objects.filter(folha__cliente=cliente, folha__competencia=competencia, folha__tipo="ADIANTAMENTO",
                                         empregado=emp).first()
            if fa:
                adiant = fa.liquido
            r = calcular_empregado(emp, competencia, cfg, tab, adiant)
        elif tipo == "ADIANTAMENTO":
            if not emp.recebe_adiantamento or emp.prolabore:
                continue
            dias = C.dias_mes_comercial(competencia, emp.admissao, emp.demissao)
            v = r2(emp.salario * cfg.adiantamento_percentual / 100 * min(dias, 15) / 15) if dias else ZERO
            it = Itens()
            it.add("ADIANT", f"Adiantamento salarial {cfg.adiantamento_percentual}%", v, inss=False, irrf=False, fgts=False)
            r = {"itens": it.lista, "proventos": v, "descontos": ZERO, "liquido": v, "base_inss": ZERO, "inss": ZERO,
                 "base_irrf": ZERO, "irrf": ZERO, "base_fgts": ZERO, "fgts": ZERO, "memoria": []}
        elif tipo in ("DECIMO1", "DECIMO2"):
            if emp.prolabore:
                continue
            r = calcular_decimo(emp, competencia.year, tipo, cfg, tab)
            if r is None:
                continue
        h = Holerite.objects.create(folha=folha, empregado=emp, itens=r["itens"], proventos=r["proventos"],
                                    descontos=r["descontos"], liquido=r["liquido"], base_inss=r["base_inss"], inss=r["inss"],
                                    base_irrf=r["base_irrf"], irrf=r["irrf"], base_fgts=r["base_fgts"], fgts=r["fgts"],
                                    memoria=r["memoria"])
        criados.append(h)
    folha.resumo = _encargos(cfg, criados)
    folha.avisos = avisos + ([] if empregados else ["Nenhum empregado ativo na competência."])
    folha.calculada_por, folha.calculada_em, folha.pagamento = usuario, timezone.now(), pagamento or folha.pagamento
    folha.save()
    return folha


# ------------------------------------------------------------------ 13º salário
def calcular_decimo(emp, ano, tipo, cfg, tab):
    fim_ano = date(ano, 12, 31)
    ate = min(emp.demissao, fim_ano) if emp.demissao else fim_ano
    if tipo == "DECIMO1":
        ja = Ferias.objects.filter(empregado=emp, adiantar_13=True, gozo_inicio__year=ano).exists()
        if ja:
            return None  # já adiantado nas férias
        # 1ª parcela: metade do 13º projetado para o ano (para quem trabalha o ano todo, metade do salário)
        avos = C.avos_13(emp.admissao, ate, ano)
    else:
        avos = C.avos_13(emp.admissao, ate, ano)
    if not avos:
        return None
    medias = media_variaveis(emp, date(ano, 11 if tipo == "DECIMO1" else 12, 1))
    base = emp.salario + medias
    if emp.insalubridade:
        base += D(tab["SALARIO_MINIMO"].dados["valor"]) * emp.insalubridade / 100
    if emp.periculosidade:
        base += emp.salario * D("0.3")
    integral = r2(base / 12 * avos)
    it, mem = Itens(), [f"Base {brl(base)} (salário + adicionais + médias {brl(medias)}) ÷ 12 × {avos} avos = {brl(integral)}"]
    if tipo == "DECIMO1":
        v = r2(integral / 2)
        it.add("13_1", "13º salário — 1ª parcela", v, ref=f"{avos}/12", inss=False, irrf=False)
        return {"itens": it.lista, "proventos": v, "descontos": ZERO, "liquido": v, "base_inss": ZERO, "inss": ZERO,
                "base_irrf": ZERO, "irrf": ZERO, "base_fgts": v, "fgts": r2(v * (D(2) if emp.tipo == "APRENDIZ" else cfg.fgts) / 100),
                "memoria": mem + ["1ª parcela: metade, sem descontos (FGTS incide)."]}
    primeira = Holerite.objects.filter(empregado=emp, folha__tipo="DECIMO1", folha__competencia__year=ano).first()
    adiant = primeira.proventos if primeira else ZERO
    fer = Ferias.objects.filter(empregado=emp, adiantar_13=True, gozo_inicio__year=ano).first()
    if fer and fer.valores:
        adiant += D(fer.valores.get("adiantamento_13", "0"))
    inss, faixas = C.inss_empregado(integral, tab["INSS"].dados)
    dep_ir, _ = _dependentes(emp, fim_ano)
    # 13º: tributação exclusiva, só deduções legais (sem desconto simplificado e sem a redução mensal)
    ir, base_ir, mem_ir = C.irrf(integral, inss, dep_ir, ZERO, tab["IRRF"].dados, redutor=False, simplificado=False)
    it.add("13_INT", "13º salário", integral, ref=f"{avos}/12")
    it.add("13_ADIANT", "(−) 1ª parcela / adiantamento", adiant, tipo="D", inss=False, irrf=False, fgts=False)
    it.add("INSS13", "INSS sobre 13º", inss, tipo="D", inss=False, irrf=False, fgts=False)
    it.add("IRRF13", "IRRF sobre 13º", ir, tipo="D", inss=False, irrf=False, fgts=False)
    proventos, descontos = it.soma("P"), it.soma("D")
    base_fgts = integral - adiant
    fgts = r2(base_fgts * (D(2) if emp.tipo == "APRENDIZ" else cfg.fgts) / 100)
    mem += [f"INSS 13º: {' + '.join(faixas)} = {brl(inss)}"] + mem_ir
    return {"itens": it.lista, "proventos": proventos, "descontos": descontos, "liquido": proventos - descontos,
            "base_inss": integral, "inss": inss, "base_irrf": base_ir, "irrf": ir, "base_fgts": base_fgts, "fgts": fgts,
            "memoria": mem}


# ------------------------------------------------------------------ férias
def validar_ferias(f):
    erros = []
    if f.dias + f.abono_dias > 30:
        erros.append("Dias de gozo + abono não podem passar de 30.")
    if f.abono_dias > 10:
        erros.append("O abono pecuniário é de no máximo 1/3 das férias (10 dias).")
    if f.dias < 5:
        erros.append("Cada período de gozo deve ter ao menos 5 dias (art. 134, §1º da CLT).")
    if f.gozo_inicio <= f.aquisitivo_fim:
        erros.append("O gozo deve começar depois do fim do período aquisitivo.")
    concessivo_fim = C.somar_meses_data(f.aquisitivo_fim, 12)
    avisos = []
    if f.gozo_fim > concessivo_fim:
        avisos.append(f"Gozo termina após o período concessivo ({concessivo_fim:%d/%m/%Y}): férias em dobro (art. 137 da CLT).")
    if f.gozo_inicio.weekday() in (4, 5):  # sexta ou sábado (antes do repouso de domingo)
        avisos.append("Início das férias nos 2 dias que antecedem feriado ou repouso semanal é vedado (art. 134, §3º).")
    return erros, avisos


def calcular_ferias(f, usuario=None):
    emp = f.empregado
    erros, avisos = validar_ferias(f)
    if erros:
        raise ErroFolha(" ".join(erros))
    tab, av_tab = tabelas(f.gozo_inicio)
    cfg = config(emp.cliente)
    medias = f.medias or media_variaveis(emp, f.gozo_inicio)
    base = emp.salario + medias
    sm = D(tab["SALARIO_MINIMO"].dados["valor"])
    if emp.insalubridade:
        base += sm * emp.insalubridade / 100
    if emp.periculosidade:
        base += emp.salario * D("0.3")
    dia = base / 30
    ferias = r2(dia * f.dias)
    terco = r2(ferias / 3)
    abono = r2(dia * f.abono_dias)
    terco_abono = r2(abono / 3)
    inss, faixas = C.inss_empregado(ferias + terco, tab["INSS"].dados)
    dep_ir, _ = _dependentes(emp, f.gozo_inicio)
    ir, base_ir, mem_ir = C.irrf(ferias + terco, inss, dep_ir, ZERO, tab["IRRF"].dados)
    adiant13 = r2(emp.salario / 2) if f.adiantar_13 else ZERO
    pensao = r2((ferias + terco + abono + terco_abono - inss - ir) * emp.pensao_percentual / 100) if emp.pensao_percentual else ZERO
    proventos = ferias + terco + abono + terco_abono + adiant13
    descontos = inss + ir + pensao
    fgts = r2((ferias + terco) * (D(2) if emp.tipo == "APRENDIZ" else cfg.fgts) / 100)
    f.valores = {
        "base_mensal": str(r2(base)), "medias": str(medias), "ferias": str(ferias), "terco": str(terco), "abono": str(abono),
        "terco_abono": str(terco_abono), "adiantamento_13": str(adiant13), "base_inss": str(ferias + terco), "inss": str(inss),
        "irrf": str(ir), "base_irrf": str(base_ir), "pensao": str(pensao), "proventos": str(proventos),
        "descontos": str(descontos), "liquido": str(proventos - descontos), "fgts": str(fgts),
        "memoria": [f"Base mensal {brl(base)} (salário + adicionais + médias {brl(medias)}) ÷ 30 = {brl(dia)} por dia",
                    f"Férias {f.dias} dias = {brl(ferias)} + 1/3 = {brl(terco)}",
                    f"Abono {f.abono_dias} dias = {brl(abono)} + 1/3 = {brl(terco_abono)} (sem INSS, IRRF e FGTS)",
                    f"INSS: {' + '.join(faixas)} = {brl(inss)} (acertado na folha do mês pelo total da competência)"] + mem_ir,
        "avisos": avisos + av_tab, "pagamento_ate": f.pagamento_ate.isoformat(),
    }
    f.calculada_por = usuario
    f.save()
    return f


# ------------------------------------------------------------------ rescisão
REGRAS_RESCISAO = {
    # tipo: (aviso_indenizado_fator, 13_prop, ferias_prop, multa_fgts_%, saque_fgts)
    "SEM_JUSTA_CAUSA": (D(1), True, True, D(40), "100%"),
    "PEDIDO": (D(0), True, True, D(0), "não"),
    "JUSTA_CAUSA": (D(0), False, False, D(0), "não"),
    "ACORDO": (D("0.5"), True, True, D(20), "80%"),
    "FIM_EXPERIENCIA": (D(0), True, True, D(0), "100%"),
    "ANTECIPADA_EMPREGADOR": (D(0), True, True, D(40), "100%"),
    "ANTECIPADA_EMPREGADO": (D(0), True, True, D(0), "não"),
}


def calcular_rescisao(r, usuario=None):
    emp = r.empregado
    if emp.prolabore:
        raise ErroFolha("Sócio com pró-labore não tem rescisão trabalhista; registre a saída no cadastro.")
    deslig = r.data_desligamento
    if deslig < emp.admissao:
        raise ErroFolha("Data de desligamento anterior à admissão.")
    fator_aviso, tem_13, tem_fp, multa_pct, saque = REGRAS_RESCISAO[r.tipo]
    tab, av_tab = tabelas(deslig)
    cfg = config(emp.cliente)
    medias = r.medias or media_variaveis(emp, deslig)
    sm = D(tab["SALARIO_MINIMO"].dados["valor"])
    base = emp.salario + medias
    if emp.insalubridade:
        base += sm * emp.insalubridade / 100
    if emp.periculosidade:
        base += emp.salario * D("0.3")
    it, mem, avisos = Itens(), [], list(av_tab)
    # saldo de salário
    comp = date(deslig.year, deslig.month, 1)
    dias_saldo = C.dias_mes_comercial(comp, emp.admissao, deslig)
    saldo = r2(emp.salario / 30 * dias_saldo)
    it.add("SALDO", "Saldo de salário", saldo, ref=f"{dias_saldo}d")
    if emp.insalubridade:
        it.add("INSALUBRIDADE", "Insalubridade proporcional", sm * emp.insalubridade / 100 * dias_saldo / 30, ref=f"{dias_saldo}d")
    if emp.periculosidade:
        it.add("PERICULOSIDADE", "Periculosidade proporcional", emp.salario * D("0.3") * dias_saldo / 30, ref=f"{dias_saldo}d")
    # aviso prévio
    dias_aviso = C.dias_aviso_previo(emp.admissao, r.data_aviso)
    projecao = deslig
    if r.aviso == "INDENIZADO" and fator_aviso:
        dias_ind = int(dias_aviso * fator_aviso)
        it.add("AVISO", "Aviso prévio indenizado", base / 30 * dias_ind, ref=f"{dias_ind}d", inss=False, irrf=False)
        projecao = deslig + timedelta(days=dias_ind)
        mem.append(f"Aviso prévio: 30 + 3 × {C.anos_completos(emp.admissao, r.data_aviso)} ano(s) = {dias_aviso} dias"
                   + (" (acordo: metade)" if fator_aviso < 1 else "") + f"; projeção do contrato até {projecao:%d/%m/%Y}")
    elif r.aviso == "DESCONTADO" and r.tipo == "PEDIDO":
        it.add("AVISO_DESC", "Aviso prévio não cumprido (desconto)", emp.salario, tipo="D", ref="30d", inss=False, irrf=False, fgts=False)
    # contratos a prazo
    if r.tipo in ("ANTECIPADA_EMPREGADOR", "ANTECIPADA_EMPREGADO"):
        if not emp.fim_contrato:
            raise ErroFolha("Informe no cadastro a data de fim do contrato a prazo.")
        restantes = max((emp.fim_contrato - deslig).days, 0)
        metade = r2(emp.salario / 30 * restantes / 2)
        if r.tipo == "ANTECIPADA_EMPREGADOR":
            it.add("ART479", "Indenização art. 479 da CLT (50% dos dias restantes)", metade, ref=f"{restantes}d", inss=False, irrf=False, fgts=False)
        else:
            it.add("ART480", "Indenização art. 480 da CLT (prejuízo ao empregador, até 50%)", metade, tipo="D", ref=f"{restantes}d",
                   inss=False, irrf=False, fgts=False)
            avisos.append("Art. 480: o desconto só é devido se houver prejuízo comprovado; ajuste se for o caso.")
    # 13º proporcional
    decimo = ZERO
    if tem_13:
        avos13 = C.avos_13(emp.admissao, projecao, projecao.year) if projecao.year == deslig.year else 12
        decimo = r2(base / 12 * avos13)
        it.add("13_PROP", "13º salário proporcional", decimo, ref=f"{avos13}/12", irrf=False)
        primeira = Holerite.objects.filter(empregado=emp, folha__tipo="DECIMO1", folha__competencia__year=deslig.year).first()
        if primeira:
            it.add("13_ADIANT", "(−) 1ª parcela do 13º já paga", primeira.proventos, tipo="D", inss=False, irrf=False, fgts=False)
        if projecao.year > deslig.year:
            avisos.append("A projeção do aviso passa para o ano seguinte: confira os avos do 13º de cada ano.")
    # férias vencidas e proporcionais
    completos, inicio_atual = C.periodos_aquisitivos(emp.admissao, projecao)
    gozados = set(Ferias.objects.filter(empregado=emp).values_list("aquisitivo_inicio", flat=True))
    vencidos = [p for p in completos if p[0] not in gozados]
    dias_venc = r.ferias_vencidas_dias if r.ferias_vencidas_dias is not None else 30 * len(vencidos)
    if dias_venc:
        fv = r2(base / 30 * dias_venc)
        it.add("FERIAS_VENC", "Férias vencidas indenizadas", fv, ref=f"{dias_venc}d", inss=False, irrf=False, fgts=False)
        it.add("TERCO_VENC", "1/3 sobre férias vencidas", fv / 3, inss=False, irrf=False, fgts=False)
        if len(vencidos) >= 2:
            avisos.append("Há mais de um período vencido: o vencido fora do período concessivo é devido em dobro (art. 137).")
    if tem_fp:
        avos_f = C.avos_ferias(inicio_atual, projecao)
        fp = r2(base / 12 * avos_f)
        it.add("FERIAS_PROP", "Férias proporcionais indenizadas", fp, ref=f"{avos_f}/12", inss=False, irrf=False, fgts=False)
        it.add("TERCO_PROP", "1/3 sobre férias proporcionais", fp / 3, inss=False, irrf=False, fgts=False)
    # impostos: saldo (tabela mensal), 13º (exclusivo); verbas indenizatórias isentas
    dep_ir, _ = _dependentes(emp, deslig)
    base_inss_mes = sum((D(i["valor"]) for i in it.lista if i["tipo"] == "P" and i["inss"] and not i["codigo"].startswith("13")), ZERO)
    inss_mes, _f = C.inss_empregado(base_inss_mes, tab["INSS"].dados)
    inss_13, _f2 = C.inss_empregado(decimo, tab["INSS"].dados) if decimo else (ZERO, [])
    rend_ir = sum((D(i["valor"]) for i in it.lista if i["tipo"] == "P" and i["irrf"]), ZERO)
    ir_mes, _b, mem_ir = C.irrf(rend_ir, inss_mes, dep_ir, ZERO, tab["IRRF"].dados)
    ir_13 = ZERO
    if decimo:
        ir_13, _b2, mem13 = C.irrf(decimo, inss_13, dep_ir, ZERO, tab["IRRF"].dados, redutor=False, simplificado=False)
    it.add("INSS", "INSS", inss_mes, tipo="D", inss=False, irrf=False, fgts=False)
    it.add("INSS13", "INSS sobre 13º", inss_13, tipo="D", inss=False, irrf=False, fgts=False)
    it.add("IRRF", "IRRF", ir_mes, tipo="D", inss=False, irrf=False, fgts=False)
    it.add("IRRF13", "IRRF sobre 13º", ir_13, tipo="D", inss=False, irrf=False, fgts=False)
    it.add("OUTROS_DESC", "Outros descontos", r.outros_descontos, tipo="D", inss=False, irrf=False, fgts=False)
    # FGTS
    fgts_aliq = D(2) if emp.tipo == "APRENDIZ" else cfg.fgts
    base_fgts = sum((D(i["valor"]) for i in it.lista if i["tipo"] == "P" and i["fgts"]), ZERO)
    base_fgts -= sum((D(i["valor"]) for i in it.lista if i["codigo"] == "13_ADIANT"), ZERO)  # FGTS da 1ª parcela já depositado
    fgts_mes = r2(base_fgts * fgts_aliq / 100)
    multa = r2((r.saldo_fgts + fgts_mes) * multa_pct / 100)
    if multa_pct and not r.saldo_fgts:
        avisos.append("Informe o saldo do FGTS para fins rescisórios (extrato do FGTS Digital) para calcular a multa.")
    proventos, descontos = it.soma("P"), it.soma("D")
    mem += [f"Base das verbas: {brl(base)} (salário + adicionais + médias {brl(medias)})",
            f"INSS (saldo): base {brl(base_inss_mes)} = {brl(inss_mes)}; INSS 13º = {brl(inss_13)}",
            "Aviso indenizado, férias indenizadas + 1/3 e indenizações dos arts. 479/480: sem INSS e sem IRRF."] + mem_ir
    r.valores = {
        "itens": it.lista, "proventos": str(proventos), "descontos": str(descontos), "liquido": str(proventos - descontos),
        "fgts_mes": str(fgts_mes), "base_fgts": str(base_fgts), "multa_fgts": str(multa), "multa_pct": str(multa_pct),
        "saque_fgts": saque, "seguro_desemprego": r.tipo == "SEM_JUSTA_CAUSA", "dias_aviso": dias_aviso,
        "projecao": projecao.isoformat(), "prazo_pagamento": r.prazo_pagamento.isoformat(),
        "periodos_vencidos": [[a.isoformat(), b.isoformat()] for a, b in vencidos], "memoria": mem, "avisos": avisos,
    }
    r.calculada_por = usuario
    r.save()
    return r


# ------------------------------------------------------------------ fechamento e integrações
@transaction.atomic
def fechar(folha, usuario):
    tab, _ = tabelas(date(folha.competencia.year, folha.competencia.month,
                          C.ultimo_dia(folha.competencia.year, folha.competencia.month)))
    pendentes = [str(t) for t in tab.values() if not t.conferida]
    if pendentes:
        raise ErroFolha("Confira antes as tabelas: " + "; ".join(pendentes) + " (Folha > Tabelas legais).")
    if not folha.calculada_em:
        raise ErroFolha("Calcule a folha antes de fechar.")
    folha.fechada, folha.fechada_por, folha.fechada_em = True, usuario, timezone.now()
    folha.save(update_fields=["fechada", "fechada_por", "fechada_em"])
    atualizar_fator_r(folha.cliente, folha.competencia)


def reabrir(folha, usuario):
    if not usuario.is_staff:
        raise ErroFolha("Somente administradores reabrem folhas.")
    folha.fechada = False
    folha.save(update_fields=["fechada"])


def atualizar_fator_r(cliente, competencia):
    """Folha de salários do mês para o fator R do Simples: remunerações + pró-labore + 13º + FGTS + CPP."""
    from fiscal.models import FolhaMensal
    total = ZERO
    for f in Folha.objects.filter(cliente=cliente, competencia=competencia, fechada=True).exclude(tipo="ADIANTAMENTO"):
        total += D(f.resumo.get("base_empregados", "0")) + D(f.resumo.get("base_prolabore", "0"))
        total += D(f.resumo.get("fgts", "0")) + D(f.resumo.get("cpp", "0"))
    FolhaMensal.objects.update_or_create(cliente=cliente, competencia=competencia, defaults={"valor": r2(total)})
    return total


def contabilizar(folha, usuario, ip=None):
    """Lançamento resumo da folha no contábil (usa as contas do plano padrão)."""
    from contabil.services import ErroContabil, PartidaDados, lancar
    c = folha.cliente
    conta = {x.codigo: x for x in c.contas.filter(sintetica=False)}
    precisa = ["4.2.01.001", "4.2.01.002", "4.2.01.005", "4.2.01.006", "2.1.02.001", "2.1.02.002", "2.1.03.001",
               "2.1.03.002", "2.1.04.008"]
    faltam = [k for k in precisa if k not in conta]
    if faltam:
        raise ErroFolha("O plano de contas do cliente não tem as contas do plano padrão: " + ", ".join(faltam))
    hs = list(folha.holerites.select_related("empregado"))
    sal = sum((h.proventos for h in hs if not h.empregado.prolabore), ZERO)
    pro = sum((h.proventos for h in hs if h.empregado.prolabore), ZERO)
    inss = sum((h.inss for h in hs), ZERO)
    irrf = sum((h.irrf for h in hs), ZERO)
    outros = sum((h.descontos - h.inss - h.irrf for h in hs), ZERO)
    fgts = sum((h.fgts for h in hs), ZERO)
    patronal = sum((D(folha.resumo.get(k, "0")) for k in ("cpp", "rat", "terceiros")), ZERO)
    liq_emp = sum((h.liquido for h in hs if not h.empregado.prolabore), ZERO)
    liq_pro = sum((h.liquido for h in hs if h.empregado.prolabore), ZERO)
    P = PartidaDados
    partidas = [P(conta["4.2.01.001"], "D", sal), P(conta["4.2.01.002"], "D", pro), P(conta["4.2.01.005"], "D", patronal),
                P(conta["4.2.01.006"], "D", fgts),
                P(conta["2.1.02.001"], "C", liq_emp + outros), P(conta["2.1.02.002"], "C", liq_pro),
                P(conta["2.1.03.001"], "C", inss + patronal), P(conta["2.1.03.002"], "C", fgts), P(conta["2.1.04.008"], "C", irrf)]
    partidas = [p for p in partidas if p.valor > 0]
    data_l = date(folha.competencia.year, folha.competencia.month, C.ultimo_dia(folha.competencia.year, folha.competencia.month))
    try:
        return lancar(c, data_l, f"{folha.get_tipo_display()} {folha.competencia:%m/%Y}", partidas, usuario, origem="IMPORTACAO",
                      origem_ref=f"FOLHA-{folha.pk}", ip=ip)
    except ErroContabil as e:
        raise ErroFolha(" ".join(e.messages)) from e
