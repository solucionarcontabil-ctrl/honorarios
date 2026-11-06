"""Regras do módulo de gestão: catálogo, geração das obrigações do mês, certificados."""
from datetime import date

from django.db import transaction
from django.utils import timezone

from cobranca.models import Cliente

from .calendario import Calendario
from .models import ClienteObrigacao, Entrega, Obrigacao

AVISO_PRAZOS = (
    "Prazo sugerido no cadastro inicial do sistema. Confira com a legislação vigente "
    "(e com o seu estado/município) e ajuste se necessário."
)

PJ = "SIMPLES,PRESUMIDO,REAL,IMUNE,OUTRO"

# sigla, nome, órgão, periodicidade, regra, dia, meses_após, mês_anual, ajuste, regimes, só c/ funcionários
CATALOGO_PADRAO = [
    ("DAS", "PGDAS-D e DAS — Simples Nacional", "RFB", "MENSAL", "DIA_FIXO", 20, 1, None, "POSTERGA", "SIMPLES", False),
    ("DAS-MEI", "DAS do MEI", "RFB", "MENSAL", "DIA_FIXO", 20, 1, None, "POSTERGA", "MEI", False),
    ("FOLHA", "Processar folha / pró-labore", "INTERNO", "MENSAL", "DIA_UTIL", 3, 1, None, "MANTEM", PJ, True),
    ("ESOCIAL", "eSocial — fechamento dos eventos periódicos", "MTE", "MENSAL", "DIA_FIXO", 15, 1, None, "ANTECIPA", PJ, True),
    ("FGTS", "FGTS Digital — guia", "CAIXA", "MENSAL", "DIA_FIXO", 20, 1, None, "ANTECIPA", PJ, True),
    ("REINF", "EFD-Reinf", "RFB", "MENSAL", "DIA_FIXO", 15, 1, None, "ANTECIPA", PJ, False),
    ("DCTFWEB", "DCTFWeb (com MIT) e DARF", "RFB", "MENSAL", "ULTIMO_DIA_UTIL", 0, 1, None, "MANTEM", PJ, False),
    ("PIS-COFINS", "DARF PIS/COFINS", "RFB", "MENSAL", "DIA_FIXO", 25, 1, None, "ANTECIPA", "PRESUMIDO,REAL", False),
    ("EFD-CONTRIB", "EFD-Contribuições", "RFB", "MENSAL", "DIA_UTIL", 10, 2, None, "MANTEM", "PRESUMIDO,REAL", False),
    ("SPED-FISCAL", "EFD ICMS/IPI (SPED Fiscal)", "SEFAZ", "MENSAL", "DIA_FIXO", 20, 1, None, "POSTERGA", "PRESUMIDO,REAL", False),
    ("ISS", "ISS / declaração de serviços municipal", "PREFEITURA", "MENSAL", "DIA_FIXO", 10, 1, None, "POSTERGA", "PRESUMIDO,REAL", False),
    ("IRPJ-CSLL", "DARF IRPJ/CSLL trimestral", "RFB", "TRIMESTRAL", "ULTIMO_DIA_UTIL", 0, 1, None, "MANTEM", "PRESUMIDO,REAL", False),
    ("DEFIS", "DEFIS — Simples Nacional", "RFB", "ANUAL", "ULTIMO_DIA_UTIL", 0, 0, 3, "MANTEM", "SIMPLES", False),
    ("DASN-SIMEI", "DASN-SIMEI — declaração anual do MEI", "RFB", "ANUAL", "ULTIMO_DIA_UTIL", 0, 0, 5, "MANTEM", "MEI", False),
    ("IRPF", "Declaração do IRPF", "RFB", "ANUAL", "ULTIMO_DIA_UTIL", 0, 0, 5, "MANTEM", "PF", False),
    ("ECD", "ECD — Escrituração Contábil Digital", "RFB", "ANUAL", "ULTIMO_DIA_UTIL", 0, 0, 6, "MANTEM", "PRESUMIDO,REAL,IMUNE", False),
    ("ECF", "ECF — Escrituração Contábil Fiscal", "RFB", "ANUAL", "ULTIMO_DIA_UTIL", 0, 0, 7, "MANTEM", "PRESUMIDO,REAL,IMUNE", False),
    ("BALANCO", "Fechamento do balanço anual", "INTERNO", "ANUAL", "ULTIMO_DIA_UTIL", 0, 0, 4, "MANTEM", "SIMPLES,PRESUMIDO,REAL,IMUNE", False),
]


def carregar_catalogo_padrao():
    criadas = 0
    for sig, nome, orgao, per, regra, dia, meses, mes_anual, ajuste, regimes, func in CATALOGO_PADRAO:
        _, novo = Obrigacao.objects.get_or_create(sigla=sig, defaults=dict(
            nome=nome, orgao=orgao, periodicidade=per, regra_prazo=regra, dia=dia or 1, meses_apos=meses,
            mes_anual=mes_anual, ajuste=ajuste, regimes=regimes, so_com_funcionarios=func, observacao=AVISO_PRAZOS,
        ))
        criadas += novo
    return criadas


@transaction.atomic
def aplicar_padrao_regime(cliente, substituir=False):
    """Vincula ao cliente as obrigações que valem para o regime dele."""
    if substituir:
        cliente.obrigacoes.all().delete()
    novas = 0
    for ob in Obrigacao.objects.filter(ativa=True):
        if ob.aplica_ao_cliente(cliente):
            _, criado = ClienteObrigacao.objects.get_or_create(cliente=cliente, obrigacao=ob)
            novas += criado
    return novas


def previa_mes(ano, mes):
    """O que vence no mês (ano, mes) para cada cliente ativo."""
    cal = Calendario()
    itens = []
    vinculos = (ClienteObrigacao.objects.filter(cliente__ativo=True, obrigacao__ativa=True)
                .select_related("cliente", "cliente__responsavel", "obrigacao", "responsavel"))
    existentes = set(Entrega.objects.filter(obrigacao__isnull=False)
                     .values_list("cliente_id", "obrigacao_id", "competencia"))
    prazos = {}
    for v in vinculos:
        ob = v.obrigacao
        comp = ob.competencia_para_vencimento(ano, mes)
        if comp is None:
            continue
        if ob.pk not in prazos:
            prazos[ob.pk] = ob.prazo_no_mes(ano, mes, cal)
        rotulo = str(comp.year) if ob.periodicidade == "ANUAL" else f"{comp:%m/%Y}"
        itens.append({
            "vinculo": v, "obrigacao": ob, "cliente": v.cliente, "competencia": comp, "prazo": prazos[ob.pk],
            "titulo": f"{ob.sigla} {rotulo}",
            "responsavel": v.responsavel or v.cliente.responsavel,
            "ja_existe": (v.cliente_id, ob.pk, comp) in existentes,
        })
    itens.sort(key=lambda i: (i["prazo"], str(i["cliente"]), i["obrigacao"].sigla))
    return itens


@transaction.atomic
def gerar_mes(ano, mes, usuario=None):
    criadas = []
    for i in previa_mes(ano, mes):
        if i["ja_existe"]:
            continue
        criadas.append(Entrega.objects.create(
            cliente=i["cliente"], obrigacao=i["obrigacao"], titulo=i["titulo"], competencia=i["competencia"],
            prazo=i["prazo"], responsavel=i["responsavel"], criado_por=usuario,
        ))
    return criadas


# ----------------------------------------------------------------------
# Certificado A1: lê validade/titular do arquivo .pfx (o arquivo NÃO é guardado)
# ----------------------------------------------------------------------
class CertificadoInvalido(Exception):
    pass


def ler_pfx(conteudo, senha):
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    try:
        _, cert, _ = pkcs12.load_key_and_certificates(conteudo, senha.encode() if senha else None)
    except ValueError as e:
        raise CertificadoInvalido("Não foi possível abrir o certificado. Confira a senha.") from e
    if cert is None:
        raise CertificadoInvalido("O arquivo não contém certificado.")

    def cn(nome):
        attrs = nome.get_attributes_for_oid(NameOID.COMMON_NAME)
        return attrs[0].value if attrs else ""

    titular = cn(cert.subject)
    documento = ""
    if ":" in titular:  # padrão ICP-Brasil: "NOME:CNPJ/CPF"
        titular, documento = titular.rsplit(":", 1)
    tz = timezone.get_current_timezone()
    return {
        "titular": titular.strip(),
        "documento": "".join(c for c in documento if c.isdigit()),
        "emissor": cn(cert.issuer),
        "validade_inicio": cert.not_valid_before_utc.astimezone(tz).date(),
        "validade_fim": cert.not_valid_after_utc.astimezone(tz).date(),
    }


def cliente_por_documento(doc):
    if not doc:
        return None
    return Cliente.objects.filter(documento=doc).first() or (
        Cliente.objects.filter(documento__startswith=doc[:8]).first() if len(doc) == 14 else None
    )


def mes_atual():
    hoje = timezone.localdate()
    return date(hoje.year, hoje.month, 1)
