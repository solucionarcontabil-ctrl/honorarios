"""Importação de clientes de planilha (Excel .xlsx ou .csv).

Pensado para a lista de empresas exportada do Domínio Web, da Contmatic ou de
qualquer planilha: as colunas são reconhecidas pelo nome do cabeçalho.
"""
import csv
import io
import unicodedata

from .models import Cliente
from .validators import cnpj_valido, cpf_valido, so_digitos

ALIASES = {
    "nome": ["razao social", "nome empresarial", "nome da empresa", "empresa", "nome", "razao"],
    "nome_fantasia": ["nome fantasia", "fantasia", "apelido"],
    "documento": ["cnpj/cpf", "cpf/cnpj", "cnpj", "cpf", "inscricao federal", "documento", "cnpj cpf", "cpf cnpj"],
    "email": ["e-mail", "email", "e mail"],
    "whatsapp": ["whatsapp", "celular", "telefone", "fone", "tel"],
    "regime": ["regime tributario", "regime", "tributacao", "forma de tributacao"],
    "inscricao_estadual": ["inscricao estadual", "insc estadual", "ie"],
    "inscricao_municipal": ["inscricao municipal", "insc municipal", "im", "ccm"],
    "contato": ["contato", "pessoa de contato", "socio", "responsavel legal"],
    "cep": ["cep"],
    "endereco": ["endereco", "logradouro", "rua"],
    "numero": ["numero", "nro", "n"],
    "bairro": ["bairro"],
    "cidade": ["municipio", "cidade"],
    "uf": ["uf", "estado"],
}


def _norm(texto):
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(texto.lower().replace(".", " ").replace("_", " ").replace(":", " ").split())


def mapear_cabecalho(cabecalho):
    """Retorna {campo: índice da coluna}."""
    mapa = {}
    normalizado = [_norm(c) for c in cabecalho]
    for campo, nomes in ALIASES.items():
        for nome in nomes:  # mais específico primeiro
            if nome in normalizado and normalizado.index(nome) not in mapa.values():
                mapa[campo] = normalizado.index(nome)
                break
    return mapa


def regime_de(texto):
    t = _norm(texto)
    if not t:
        return ""
    if "mei" in t.split() or "microempreendedor" in t:
        return "MEI"
    if "simples" in t:
        return "SIMPLES"
    if "presumido" in t:
        return "PRESUMIDO"
    if "real" in t:
        return "REAL"
    if "imune" in t or "isent" in t:
        return "IMUNE"
    if "fisica" in t:
        return "PF"
    return "OUTRO"


def normalizar_documento(valor):
    if isinstance(valor, float):
        valor = f"{valor:.0f}"
    d = so_digitos(str(valor or ""))
    if not d:
        return ""
    if len(d) <= 11 and cpf_valido(d.zfill(11)):
        return d.zfill(11)
    if len(d) <= 14 and cnpj_valido(d.zfill(14)):
        return d.zfill(14)
    return d


def ler_linhas(nome_arquivo, conteudo):
    if nome_arquivo.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        return [list(r) for r in wb.active.iter_rows(values_only=True)]
    for enc in ("utf-8-sig", "cp1252"):
        try:
            texto = conteudo.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialeto = csv.Sniffer().sniff(texto[:4000], delimiters=";,\t")
    except csv.Error:
        dialeto = csv.excel
        dialeto.delimiter = ";"
    return [r for r in csv.reader(io.StringIO(texto), dialeto)]


def analisar(nome_arquivo, conteudo):
    """Lê a planilha e devolve (linhas_analisadas, mapa, erro_geral)."""
    linhas = ler_linhas(nome_arquivo, conteudo)
    # acha o cabeçalho nas 15 primeiras linhas (relatórios costumam ter título em cima)
    for idx, linha in enumerate(linhas[:15]):
        mapa = mapear_cabecalho(linha)
        if "documento" in mapa and "nome" in mapa:
            break
    else:
        return [], {}, "Não encontrei as colunas de nome/razão social e CNPJ/CPF no cabeçalho da planilha."

    existentes = dict(Cliente.objects.values_list("documento", "id"))
    vistos, resultado = set(), []
    for num, linha in enumerate(linhas[idx + 1:], start=idx + 2):
        def val(campo):
            i = mapa.get(campo)
            if i is None or i >= len(linha) or linha[i] is None:
                return ""
            v = linha[i]
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            return str(v).strip()

        if not any(str(c or "").strip() for c in linha):
            continue
        dados = {campo: val(campo) for campo in mapa}
        dados["documento"] = normalizar_documento(linha[mapa["documento"]] if mapa["documento"] < len(linha) else "")
        if "regime" in dados:
            dados["regime"] = regime_de(dados["regime"])
        dados["uf"] = dados.get("uf", "")[:2].upper()
        doc = dados["documento"]
        if not dados.get("nome"):
            situacao, motivo = "erro", "sem nome"
        elif not (len(doc) == 11 and cpf_valido(doc)) and not (len(doc) == 14 and cnpj_valido(doc)):
            situacao, motivo = "erro", "CNPJ/CPF inválido"
        elif doc in vistos:
            situacao, motivo = "erro", "repetido na planilha"
        elif doc in existentes:
            situacao, motivo = "existe", "já cadastrado — completa só campos vazios"
        else:
            situacao, motivo = "novo", ""
        vistos.add(doc)
        resultado.append({"linha": num, "dados": dados, "situacao": situacao, "motivo": motivo})
    return resultado, mapa, None


CAMPOS_TEXTO = [c for c in ALIASES if c != "documento"]


def _cortar(campo, valor):
    limite = Cliente._meta.get_field(campo).max_length
    return valor[:limite] if limite else valor


def importar(linhas, responsavel=None):
    criados = atualizados = 0
    for item in linhas:
        d = item["dados"]
        if item["situacao"] == "novo":
            campos = {c: _cortar(c, d[c]) for c in CAMPOS_TEXTO if d.get(c)}
            campos.setdefault("regime", "SIMPLES" if len(d["documento"]) == 14 else "PF")
            Cliente.objects.create(documento=d["documento"], responsavel=responsavel, **campos)
            criados += 1
        elif item["situacao"] == "existe":
            cli = Cliente.objects.get(documento=d["documento"])
            mudou = False
            for c in CAMPOS_TEXTO:
                if d.get(c) and not getattr(cli, c):
                    setattr(cli, c, _cortar(c, d[c]))
                    mudou = True
            if mudou:
                cli.save()
                atualizados += 1
    return criados, atualizados
