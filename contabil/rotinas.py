"""Rotinas automáticas: extrato bancário (OFX) e lançamentos a partir dos XMLs."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from .models import ItemExtrato, RegraExtrato
from .services import ErroContabil, PartidaDados, conta_papel, lancar


# ------------------------------------------------------------------ OFX
class OFXInvalido(Exception):
    pass


def _tag(bloco, nome):
    m = re.search(rf"<{nome}>([^<\r\n]*)", bloco, re.I)
    return m.group(1).strip() if m else ""


def ler_ofx(conteudo):
    """Lê OFX 1.x (SGML) e 2.x (XML). Retorna lista de dicts (fitid, data, valor, descricao)."""
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            texto = conteudo.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if "<STMTTRN>" not in texto.upper():
        raise OFXInvalido("Arquivo não parece um extrato OFX (sem lançamentos <STMTTRN>).")
    itens = []
    for bloco in re.findall(r"<STMTTRN>(.*?)(?=<STMTTRN>|</BANKTRANLIST>|</STMTTRN>|$)", texto, re.I | re.S):
        bruto = _tag(bloco, "TRNAMT").replace(",", ".")
        data_txt = _tag(bloco, "DTPOSTED")[:8]
        try:
            valor = Decimal(bruto)
            dt = date(int(data_txt[:4]), int(data_txt[4:6]), int(data_txt[6:8]))
        except (InvalidOperation, ValueError):
            continue
        descricao = " ".join(x for x in (_tag(bloco, "NAME"), _tag(bloco, "MEMO")) if x) or _tag(bloco, "TRNTYPE")
        fitid = _tag(bloco, "FITID") or f"{dt:%Y%m%d}-{valor}-{descricao[:30]}"
        itens.append({"fitid": fitid[:80], "data": dt, "valor": valor, "descricao": descricao[:255]})
    if not itens:
        raise OFXInvalido("Nenhum lançamento encontrado no extrato.")
    return itens


def sugerir(cliente, descricao, valor):
    sentido = "ENTRADA" if valor > 0 else "SAIDA"
    texto = descricao.upper()
    for r in RegraExtrato.objects.filter(cliente=cliente).select_related("conta"):
        if r.sentido in ("AMBOS", sentido) and r.texto.upper() in texto:
            return r
    return None


def importar_ofx(cliente, conta_banco, conteudo):
    if conta_banco.cliente_id != cliente.pk or conta_banco.sintetica:
        raise ErroContabil("Escolha uma conta bancária analítica deste cliente.")
    novos = repetidos = 0
    for it in ler_ofx(conteudo):
        regra = sugerir(cliente, it["descricao"], it["valor"])
        try:
            with transaction.atomic():
                ItemExtrato.objects.create(cliente=cliente, conta_banco=conta_banco, contrapartida=regra.conta if regra else None,
                                           **it)
            novos += 1
        except IntegrityError:
            repetidos += 1
    return novos, repetidos


@transaction.atomic
def contabilizar_itens(cliente, itens, usuario, ip=None):
    feitos, erros = 0, []
    for item in itens:
        if item.status != ItemExtrato.PENDENTE or not item.contrapartida:
            continue
        regra = sugerir(cliente, item.descricao, item.valor)
        historico = (regra.historico if regra and regra.historico and regra.conta_id == item.contrapartida_id
                     else item.descricao)
        v = abs(item.valor)
        if item.valor > 0:  # entrada: D banco / C contrapartida
            partidas = [PartidaDados(item.conta_banco, "D", v), PartidaDados(item.contrapartida, "C", v)]
        else:
            partidas = [PartidaDados(item.contrapartida, "D", v), PartidaDados(item.conta_banco, "C", v)]
        try:
            with transaction.atomic():
                lanc = lancar(cliente, item.data, historico, partidas, usuario, origem="OFX",
                              origem_ref=f"{item.conta_banco_id}:{item.fitid}", ip=ip)
        except ErroContabil as e:
            erros.append(f"{item.data:%d/%m} {item.descricao[:40]}: {' '.join(e.messages)}")
            continue
        item.lancamento, item.status = lanc, ItemExtrato.CONTABILIZADO
        item.save(update_fields=["lancamento", "status"])
        feitos += 1
    return feitos, erros


# ------------------------------------------------------------------ notas fiscais -> lançamentos
# Regra padrão (confira com a operação do cliente): considera receita/compra só os CFOPs de
# venda/serviço. Ficam de fora: outras saídas/entradas (x.9xx: remessas, bonificação, comodato),
# devoluções (x.2xx), transferências (x.15x, x.408/409, x.658/659), ativo e uso/consumo (x.55x),
# créditos de ICMS (x.60x), devoluções/remessas com ST e de combustíveis.
EXCLUIDOS = {"408", "409", "410", "411", "412", "413", "414", "415",
             "658", "659", "660", "661", "662", "663", "664", "665", "666"}


def cfop_operacional(cfop, entrada):
    """True se o CFOP é venda/serviço (saída: 5/6/7xxx) ou compra (entrada: 1/2/3xxx)."""
    if not (cfop and len(cfop) == 4 and cfop.isdigit()):
        return False
    grupo, resto = cfop[0], cfop[1:]
    if grupo not in ("123" if entrada else "567"):
        return False
    if resto[0] in "92" or resto[:2] in ("15", "55", "60") or resto in EXCLUIDOS:
        return False
    return True


def valor_operacional(doc, entrada):
    if doc.modelo == "NFSE":
        return doc.valor_total
    if not doc.cfops:  # sem detalhe de CFOP: usa o total
        return doc.valor_total
    return sum((Decimal(v) for c, v in doc.cfops.items() if cfop_operacional(c, entrada)), Decimal("0"))


def previa_notas(cliente, competencia):
    """Lista o que seria lançado a partir das notas da competência (só autorizadas, só operacionais)."""
    from xmls.services import emitidas, recebidas
    ja = set(cliente.lancamentos.filter(origem="XML").values_list("origem_ref", flat=True))
    itens = []
    for doc in emitidas(cliente, competencia).filter(situacao="AUTORIZADA", tipo_operacao="SAIDA"):
        v = valor_operacional(doc, entrada=False)
        if v > 0:
            itens.append({"doc": doc, "tipo": "SAIDA", "valor": v, "ref": f"S:{doc.chave}"[:80],
                          "ja": f"S:{doc.chave}"[:80] in ja})
    # Notas recebidas (compras). No XML do fornecedor os CFOPs são os de SAÍDA dele (5/6xxx),
    # por isso a regra de saída é usada. Serviços tomados (NFS-e) e fretes (CT-e) ficam para
    # lançamento manual, porque a conta depende do caso (custo ou despesa).
    for doc in recebidas(cliente, competencia).filter(situacao="AUTORIZADA", modelo="NFE", tipo_operacao="SAIDA"):
        v = valor_operacional(doc, entrada=False)
        if v > 0:
            itens.append({"doc": doc, "tipo": "ENTRADA", "valor": v, "ref": f"E:{doc.chave}"[:80],
                          "ja": f"E:{doc.chave}"[:80] in ja})
    return itens


@transaction.atomic
def contabilizar_notas(cliente, competencia, usuario, ip=None, controla_estoque=True):
    clientes_c = conta_papel(cliente, "CLIENTES")
    fornec = conta_papel(cliente, "FORNECEDORES")
    rec_vendas = conta_papel(cliente, "RECEITA_VENDAS")
    rec_serv = conta_papel(cliente, "RECEITA_SERVICOS")
    destino_compra = conta_papel(cliente, "ESTOQUE" if controla_estoque else "CUSTO_COMPRAS")
    feitos, erros = 0, []
    for it in previa_notas(cliente, competencia):
        if it["ja"]:
            continue
        d = it["doc"]
        if it["tipo"] == "SAIDA":
            receita = rec_serv if d.modelo in ("NFSE", "CTE") else rec_vendas
            partidas = [PartidaDados(clientes_c, "D", it["valor"]), PartidaDados(receita, "C", it["valor"])]
            hist = f"{d.get_modelo_display()} {d.numero} — {d.dest_nome or 'consumidor'}"
        else:
            partidas = [PartidaDados(destino_compra, "D", it["valor"]), PartidaDados(fornec, "C", it["valor"])]
            hist = f"Compra {d.get_modelo_display()} {d.numero} — {d.emit_nome}"
        try:
            with transaction.atomic():
                lancar(cliente, d.emissao, hist, partidas, usuario, documento=d.numero, origem="XML",
                       origem_ref=it["ref"], ip=ip)
            feitos += 1
        except ErroContabil as e:
            erros.append(f"{d.numero}: {' '.join(e.messages)}")
    return feitos, erros
