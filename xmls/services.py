"""Importação de XMLs (soltos ou em ZIP), vínculo com clientes e relatórios."""
import gzip
import io
import zipfile
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum

from cobranca.models import Cliente

from . import parser
from .models import Documento, Evento, Lote

LIMITE_ZIP_DESCOMPACTADO = 1024 * 1024 * 1024  # 1 GB
LIMITE_ARQUIVOS_ZIP = 50_000
LIMITE_XML = 5 * 1024 * 1024  # um XML fiscal raramente passa de 1 MB

# Limites anuais (receita bruta). Confira se houve mudança na legislação.
LIMITE_SIMPLES = Decimal("4800000.00")
SUBLIMITE_ICMS_ISS = Decimal("3600000.00")
LIMITE_MEI = Decimal("81000.00")


def _arquivos(nome, conteudo):
    """Gera (nome, bytes) de cada XML, abrindo ZIPs (inclusive ZIP dentro de ZIP, 1 nível)."""
    if nome.lower().endswith(".zip") or conteudo[:4] == b"PK\x03\x04":
        try:
            z = zipfile.ZipFile(io.BytesIO(conteudo))
        except zipfile.BadZipFile:
            yield nome, None, "ZIP corrompido"
            return
        membros = [m for m in z.infolist() if not m.is_dir()]
        if len(membros) > LIMITE_ARQUIVOS_ZIP:
            yield nome, None, f"ZIP com mais de {LIMITE_ARQUIVOS_ZIP} arquivos"
            return
        if sum(m.file_size for m in membros) > LIMITE_ZIP_DESCOMPACTADO:
            yield nome, None, "ZIP grande demais depois de descompactado"
            return
        for m in membros:
            base = m.filename.rsplit("/", 1)[-1]
            if base.startswith(".") or "__MACOSX" in m.filename:
                continue
            if base.lower().endswith(".zip"):
                interno = z.read(m)
                for sub in _arquivos(base, interno):
                    if not sub[0].lower().endswith(".zip"):
                        yield sub
                continue
            if base.lower().endswith(".xml"):
                if m.file_size > LIMITE_XML:
                    yield base, None, "XML grande demais"
                    continue
                yield base, z.read(m), None
        return
    if len(conteudo) > LIMITE_XML:
        yield nome, None, "XML grande demais"
        return
    yield nome, conteudo, None


class _Clientes:
    def __init__(self):
        self.por_doc = dict(Cliente.objects.values_list("documento", "id"))
        self.por_raiz = {}
        for doc, cid in self.por_doc.items():
            if len(doc) == 14:
                self.por_raiz.setdefault(doc[:8], cid)  # filiais pela raiz do CNPJ

    def achar(self, doc):
        if not doc:
            return None
        return self.por_doc.get(doc) or (self.por_raiz.get(doc[:8]) if len(doc) == 14 else None)


def _aplicar_cancelamentos(chaves):
    n = 0
    chaves = list(set(chaves))
    for i in range(0, len(chaves), 500):
        parte = chaves[i:i + 500]
        canceladas = list(Evento.objects.filter(chave__in=parte, tipo__in=parser.EVENTOS_CANCELAMENTO)
                          .values_list("chave", flat=True))
        n += Documento.objects.filter(chave__in=canceladas).exclude(situacao="CANCELADA").update(situacao="CANCELADA")
    return n


def importar(arquivos, usuario=None):
    """arquivos = lista de (nome, bytes). Retorna o Lote com o resumo."""
    lote = Lote.objects.create(enviado_por=usuario)
    clientes = _Clientes()
    erros, chaves_novas = [], []
    for nome_env, conteudo_env in arquivos:
        for nome, conteudo, erro in _arquivos(nome_env, conteudo_env):
            lote.arquivos += 1
            if erro:
                lote.erros += 1
                erros.append(f"{nome}: {erro}")
                continue
            try:
                docs, eventos = parser.ler(conteudo)
            except parser.XMLNaoReconhecido as e:
                lote.erros += 1
                erros.append(f"{nome}: {e}")
                continue
            gz = gzip.compress(conteudo)
            for ev in eventos:
                try:
                    with transaction.atomic():
                        Evento.objects.create(chave=ev["chave"], tipo=ev["tipo"], descricao=ev["descricao"][:100],
                                              data=ev["data"], xml_gz=gz)
                    lote.eventos += 1
                    chaves_novas.append(ev["chave"])
                except IntegrityError:
                    lote.repetidos += 1
            for d in docs:
                if Documento.objects.filter(chave=d["chave"]).exists():
                    lote.repetidos += 1
                    continue
                emit = clientes.achar(d["emit_doc"])
                dest = clientes.achar(d["dest_doc"])
                if not emit and not dest:
                    lote.sem_cliente += 1
                emissao = d["emissao"]
                try:
                    with transaction.atomic():
                        Documento.objects.create(
                            chave=d["chave"], modelo=d["modelo"], numero=d["numero"][:20], serie=d["serie"][:5],
                            emissao=emissao, competencia=emissao.replace(day=1) if emissao else None,
                            emit_doc=d["emit_doc"][:14], emit_nome=d["emit_nome"][:150], emit_uf=d["emit_uf"][:2],
                            dest_doc=d["dest_doc"][:14], dest_nome=d["dest_nome"][:150],
                            cliente_emitente_id=emit, cliente_destinatario_id=dest,
                            tipo_operacao=d["tipo_operacao"], natureza=d["natureza"][:120], situacao=d["situacao"],
                            valor_total=d["valor_total"], valor_icms=d["valor_icms"], valor_icms_st=d["valor_icms_st"],
                            valor_ipi=d["valor_ipi"], valor_pis=d["valor_pis"], valor_cofins=d["valor_cofins"],
                            valor_iss=d["valor_iss"], cfops=d["cfops"], nome_arquivo=nome[:200], xml_gz=gz, lote=lote,
                        )
                    lote.novos += 1
                    chaves_novas.append(d["chave"])
                except IntegrityError:
                    lote.repetidos += 1
    _aplicar_cancelamentos(chaves_novas)
    lote.detalhes_erros = "\n".join(erros[:500])
    lote.save()
    return lote


def revincular():
    """Depois de cadastrar/importar clientes, liga os XMLs que estavam 'sem cliente'."""
    clientes = _Clientes()
    n = 0
    for d in Documento.objects.filter(Q(cliente_emitente__isnull=True) | Q(cliente_destinatario__isnull=True)):
        emit, dest = d.cliente_emitente_id or clientes.achar(d.emit_doc), d.cliente_destinatario_id or clientes.achar(d.dest_doc)
        if (emit, dest) != (d.cliente_emitente_id, d.cliente_destinatario_id):
            d.cliente_emitente_id, d.cliente_destinatario_id = emit, dest
            d.save(update_fields=["cliente_emitente", "cliente_destinatario"])
            n += 1
    return n


# --------------------------------------------------------------------- consultas
def emitidas(cliente, competencia=None):
    qs = Documento.objects.filter(cliente_emitente=cliente)
    return qs.filter(competencia=competencia) if competencia else qs


def recebidas(cliente, competencia=None):
    qs = Documento.objects.filter(cliente_destinatario=cliente).exclude(cliente_emitente=cliente)
    return qs.filter(competencia=competencia) if competencia else qs


def faturamento_qs(cliente):
    """Receita: notas de saída emitidas e autorizadas (NF-e, NFC-e, CT-e, NFS-e)."""
    return emitidas(cliente).filter(situacao="AUTORIZADA", tipo_operacao="SAIDA")


def resumo_cfop(qs):
    tot = defaultdict(Decimal)
    for cfops in qs.filter(situacao="AUTORIZADA").values_list("cfops", flat=True):
        for cfop, valor in (cfops or {}).items():
            tot[cfop] += Decimal(valor)
    return sorted(tot.items())


def somar(qs):
    r = qs.filter(situacao="AUTORIZADA").aggregate(
        total=Sum("valor_total"), icms=Sum("valor_icms"), st=Sum("valor_icms_st"), ipi=Sum("valor_ipi"),
        pis=Sum("valor_pis"), cofins=Sum("valor_cofins"), iss=Sum("valor_iss"))
    return {k: v or Decimal("0") for k, v in r.items()}


def _somar_meses(d, n):
    t = d.year * 12 + d.month - 1 + n
    return date(t // 12, t % 12 + 1, 1)


def faturamento_12m(cliente, ate):
    """Tabela mês a mês com o RBT12 (soma dos 12 meses anteriores ao mês)."""
    inicio = _somar_meses(ate, -23)
    por_mes = {r["competencia"]: r["s"] for r in faturamento_qs(cliente)
               .filter(competencia__gte=inicio, competencia__lte=ate)
               .values("competencia").annotate(s=Sum("valor_total")).order_by()}
    linhas = []
    for i in range(11, -1, -1):
        mes = _somar_meses(ate, -i)
        rbt12 = sum((por_mes.get(_somar_meses(mes, -k), Decimal("0")) for k in range(1, 13)), Decimal("0"))
        linhas.append({"mes": mes, "valor": por_mes.get(mes, Decimal("0")), "rbt12": rbt12})
    ano = sum((v for m, v in por_mes.items() if m.year == ate.year), Decimal("0"))
    return linhas, ano


def zip_xmls(qs):
    buf = io.BytesIO()
    nomes = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for d in qs.only("chave", "nome_arquivo", "xml_gz", "modelo").iterator():
            nome = f"{d.modelo}/{d.chave}.xml"
            if nome in nomes:
                continue
            nomes.add(nome)
            z.writestr(nome, d.xml)
        for ev in Evento.objects.filter(chave__in=qs.values("chave")):
            z.writestr(f"EVENTOS/{ev.chave}-{ev.tipo}-{ev.pk}.xml", gzip.decompress(bytes(ev.xml_gz)))
    return buf.getvalue()
