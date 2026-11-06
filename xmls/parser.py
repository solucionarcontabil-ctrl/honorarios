"""Leitura de XMLs fiscais: NF-e/NFC-e, CT-e, eventos, NFS-e (Nacional, ABRASF e Paulistana).

Usa defusedxml para não ser vulnerável a XMLs maliciosos. A leitura ignora
namespaces e procura as tags pelo nome, para aguentar as variações de versão.
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from defusedxml import ElementTree as ET

MODELOS = {"55": "NFE", "65": "NFCE", "57": "CTE", "67": "CTE"}


class XMLNaoReconhecido(Exception):
    pass


# --------------------------------------------------------------------- utilitários
def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def achar(el, *caminho):
    """Desce pelos nomes de tag (sem namespace). '*' aceita qualquer nível."""
    atual = [el]
    for nome in caminho:
        prox = []
        for a in atual:
            if a is None:
                continue
            for filho in (a.iter() if nome.startswith("//") else a):
                if filho is a:
                    continue
                if _local(filho.tag) == nome.lstrip("/"):
                    prox.append(filho)
                    if nome.startswith("//"):
                        break
        atual = prox
        if not atual:
            return None
    return atual[0]


def _primeiro(*els):
    for e in els:
        if e is not None:
            return e
    return None


def todos(el, nome):
    return [e for e in el.iter() if _local(e.tag) == nome]


def texto(el, *caminho):
    alvo = achar(el, *caminho) if caminho else el
    return (alvo.text or "").strip() if alvo is not None else ""


def dec(v):
    try:
        return Decimal(str(v).replace(",", ".")) if v not in ("", None) else Decimal("0")
    except InvalidOperation:
        return Decimal("0")


def data_hora(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(v[:19] if "T" in fmt else v[:10], fmt)
        except ValueError:
            continue
    return None


def documento(el):
    """CNPJ ou CPF dentro de um bloco (emit, dest, toma...)."""
    if el is None:
        return ""
    for nome in ("CNPJ", "Cnpj", "CPF", "Cpf"):
        v = texto(el, "//" + nome)
        if v:
            return re.sub(r"\D", "", v)
    return ""


def _doc_base(**kw):
    base = {
        "chave": "", "modelo": "", "numero": "", "serie": "", "emissao": None,
        "emit_doc": "", "emit_nome": "", "emit_uf": "", "dest_doc": "", "dest_nome": "",
        "tipo_operacao": "SAIDA", "natureza": "", "valor_total": Decimal("0"), "valor_icms": Decimal("0"),
        "valor_icms_st": Decimal("0"), "valor_ipi": Decimal("0"), "valor_pis": Decimal("0"),
        "valor_cofins": Decimal("0"), "valor_iss": Decimal("0"), "cfops": {}, "situacao": "AUTORIZADA",
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------- NF-e / NFC-e
def _nfe(raiz):
    inf = achar(raiz, "//infNFe")
    chave = re.sub(r"\D", "", inf.get("Id", "")) if inf is not None else ""
    ide, emit, dest = achar(inf, "ide"), achar(inf, "emit"), achar(inf, "dest")
    tot = achar(inf, "total", "ICMSTot")
    cfops = {}
    for det in todos(inf, "det"):
        cfop = texto(det, "prod", "CFOP")
        if cfop:
            cfops[cfop] = cfops.get(cfop, Decimal("0")) + dec(texto(det, "prod", "vProd"))
    c_stat = texto(raiz, "//protNFe", "//cStat")
    situacao = {"100": "AUTORIZADA", "150": "AUTORIZADA", "101": "CANCELADA", "151": "CANCELADA",
                "110": "DENEGADA", "301": "DENEGADA", "302": "DENEGADA"}.get(c_stat, "SEM_PROTOCOLO" if not c_stat else "AUTORIZADA")
    mod = texto(ide, "mod")
    return _doc_base(
        chave=chave, modelo=MODELOS.get(mod, "NFE"), numero=texto(ide, "nNF"), serie=texto(ide, "serie"),
        emissao=data_hora(texto(ide, "dhEmi") or texto(ide, "dEmi")),
        emit_doc=documento(emit), emit_nome=texto(emit, "xNome"), emit_uf=texto(emit, "//UF"),
        dest_doc=documento(dest), dest_nome=texto(dest, "xNome"),
        tipo_operacao="ENTRADA" if texto(ide, "tpNF") == "0" else "SAIDA",
        natureza=texto(ide, "natOp"),
        valor_total=dec(texto(tot, "vNF")), valor_icms=dec(texto(tot, "vICMS")), valor_icms_st=dec(texto(tot, "vST")),
        valor_ipi=dec(texto(tot, "vIPI")), valor_pis=dec(texto(tot, "vPIS")), valor_cofins=dec(texto(tot, "vCOFINS")),
        cfops={k: str(v) for k, v in cfops.items()}, situacao=situacao,
    )


# --------------------------------------------------------------------- CT-e
def _cte(raiz):
    inf = achar(raiz, "//infCte")
    chave = re.sub(r"\D", "", inf.get("Id", "")) if inf is not None else ""
    ide, emit = achar(inf, "ide"), achar(inf, "emit")
    # tomador: toma3/toma (0 rem, 1 exped, 2 receb, 3 dest) ou toma4 com documento próprio
    toma4 = achar(ide, "//toma4")
    if toma4 is not None:
        tomador = toma4
    else:
        codigo = texto(ide, "//toma") or "3"
        tomador = achar(inf, {"0": "rem", "1": "exped", "2": "receb", "3": "dest"}.get(codigo, "dest"))
    cfop = texto(ide, "CFOP")
    valor = dec(texto(inf, "vPrest", "vTPrest"))
    c_stat = texto(raiz, "//protCTe", "//cStat")
    return _doc_base(
        chave=chave, modelo="CTE", numero=texto(ide, "nCT"), serie=texto(ide, "serie"),
        emissao=data_hora(texto(ide, "dhEmi")), emit_doc=documento(emit), emit_nome=texto(emit, "xNome"),
        emit_uf=texto(emit, "//UF"), dest_doc=documento(tomador), dest_nome=texto(tomador, "//xNome"),
        natureza=texto(ide, "natOp"), valor_total=valor, valor_icms=dec(texto(inf, "imp", "//vICMS")),
        cfops={cfop: str(valor)} if cfop else {},
        situacao={"101": "CANCELADA", "110": "DENEGADA"}.get(c_stat, "AUTORIZADA" if c_stat else "SEM_PROTOCOLO"),
    )


# --------------------------------------------------------------------- NFS-e Padrão Nacional
def _nfse_nacional(raiz):
    inf = achar(raiz, "//infNFSe")
    dps = achar(inf, "//infDPS")
    emit = achar(inf, "emit")
    toma = achar(dps, "toma") if dps is not None else None
    chave = re.sub(r"\D", "", inf.get("Id", ""))
    v_serv = dec(texto(dps, "//vServ")) if dps is not None else Decimal("0")
    return _doc_base(
        chave=f"NFSE{chave}", modelo="NFSE", numero=texto(inf, "nNFSe"), serie=texto(dps, "serie") if dps is not None else "",
        emissao=data_hora(texto(dps, "dhEmi") if dps is not None else texto(inf, "dhProc")),
        emit_doc=documento(emit), emit_nome=texto(emit, "xNome"), emit_uf=texto(emit, "//UF"),
        dest_doc=documento(toma), dest_nome=texto(toma, "xNome") if toma is not None else "",
        natureza=(texto(inf, "xTribNac") or texto(dps, "//xDescServ"))[:120],
        valor_total=v_serv or dec(texto(inf, "valores", "vLiq")),
        valor_iss=dec(texto(inf, "valores", "vISSQN")),
    )


# --------------------------------------------------------------------- NFS-e ABRASF (maioria dos municípios)
def _nfse_abrasf(inf):
    prest = _primeiro(achar(inf, "//PrestadorServico"), achar(inf, "//Prestador"))
    toma = _primeiro(achar(inf, "//TomadorServico"), achar(inf, "//Tomador"))
    numero = texto(inf, "Numero")
    prest_doc = documento(prest)
    municipio = texto(inf, "//CodigoMunicipio")
    valor = dec(texto(inf, "//ValorServicos")) or dec(texto(inf, "//ValorLiquidoNfse"))
    return _doc_base(
        chave=f"NFSE-{prest_doc}-{municipio}-{numero}-{texto(inf, 'CodigoVerificacao')}"[:60],
        modelo="NFSE", numero=numero, emissao=data_hora(texto(inf, "DataEmissao") or texto(inf, "//Competencia")),
        emit_doc=prest_doc, emit_nome=texto(prest, "//RazaoSocial") if prest is not None else "",
        dest_doc=documento(toma), dest_nome=texto(toma, "//RazaoSocial") if toma is not None else "",
        natureza=texto(inf, "//Discriminacao")[:120], valor_total=valor, valor_iss=dec(texto(inf, "//ValorIss")),
    )


# --------------------------------------------------------------------- NFS-e Paulistana (São Paulo)
def _nfse_paulistana(nfe):
    numero = texto(nfe, "//NumeroNFe")
    prest_doc = documento(achar(nfe, "CPFCNPJPrestador"))
    return _doc_base(
        chave=f"NFSE-SP-{prest_doc}-{numero}-{texto(nfe, '//CodigoVerificacao')}"[:60],
        modelo="NFSE", numero=numero, emissao=data_hora(texto(nfe, "DataEmissaoNFe")),
        emit_doc=prest_doc, emit_nome=texto(nfe, "RazaoSocialPrestador"), emit_uf="SP",
        dest_doc=documento(achar(nfe, "CPFCNPJTomador")), dest_nome=texto(nfe, "RazaoSocialTomador"),
        natureza=texto(nfe, "Discriminacao")[:120],
        valor_total=dec(texto(nfe, "ValorServicos")), valor_iss=dec(texto(nfe, "ValorISS")),
        situacao="CANCELADA" if texto(nfe, "StatusNFe") == "C" else "AUTORIZADA",
    )


# --------------------------------------------------------------------- eventos
def _evento(inf):
    tipo = texto(inf, "tpEvento")
    chave = texto(inf, "chNFe") or texto(inf, "chCTe")
    return {"chave": chave, "tipo": tipo, "data": data_hora(texto(inf, "dhEvento")),
            "descricao": texto(inf, "//descEvento") or tipo}


EVENTOS_CANCELAMENTO = {"110111", "110112", "110115"}


# --------------------------------------------------------------------- entrada
def ler(conteudo):
    """Retorna (documentos, eventos). Levanta XMLNaoReconhecido se não for fiscal."""
    try:
        raiz = ET.fromstring(conteudo)
    except Exception as e:
        raise XMLNaoReconhecido(f"XML inválido: {e}") from e
    nome = _local(raiz.tag)
    docs, eventos = [], []

    if achar(raiz, "//infNFe") is not None:
        docs.append(_nfe(raiz))
    elif achar(raiz, "//infCte") is not None:
        docs.append(_cte(raiz))
    elif achar(raiz, "//infNFSe") is not None:
        docs.append(_nfse_nacional(raiz))
    elif todos(raiz, "InfNfse"):
        cancelados = {texto(c, "//Numero") for c in todos(raiz, "NfseCancelamento")}
        for inf in todos(raiz, "InfNfse"):
            d = _nfse_abrasf(inf)
            if d["numero"] in cancelados:
                d["situacao"] = "CANCELADA"
            docs.append(d)
    elif nome in ("RetornoConsulta", "RetornoXML", "NFe") and (todos(raiz, "ChaveNFe") or nome == "NFe"):
        blocos = [raiz] if nome == "NFe" else [e for e in raiz if _local(e.tag) == "NFe"]
        docs.extend(_nfse_paulistana(b) for b in blocos)

    ret = achar(raiz, "//retEvento")
    aceito = ret is None or texto(ret, "//cStat") in ("135", "136", "155")
    vistos = set()
    for inf in todos(raiz, "infEvento"):
        if aceito and texto(inf, "tpEvento") and texto(inf, "dhEvento"):
            ev = _evento(inf)
            if ev["chave"] and (ev["chave"], ev["tipo"]) not in vistos:
                vistos.add((ev["chave"], ev["tipo"]))
                eventos.append(ev)

    if not docs and not eventos:
        raise XMLNaoReconhecido(f"Tipo de XML não reconhecido (raiz <{nome}>).")
    for d in docs:
        if not d["chave"] or not d["emit_doc"]:
            raise XMLNaoReconhecido("XML fiscal sem chave ou sem emitente.")
        if isinstance(d["emissao"], datetime):
            d["emissao"] = d["emissao"].date()
        elif not isinstance(d["emissao"], date):
            d["emissao"] = None
    return docs, eventos
