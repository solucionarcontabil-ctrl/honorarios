"""Importação de clientes de planilha (Excel .xlsx/.xls ou .csv).

Aceita dois formatos:
- tabela com cabeçalho (qualquer planilha): colunas reconhecidas pelo nome;
- relatório "Empresas — dados cadastrais" do Domínio (uma ficha por empresa, rótulo: valor).
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
    "observacoes": ["observacoes", "observacao"],
    "inicio_atividade": ["inicio atividades", "inicio das atividades", "data de abertura", "abertura"],
}
CAMPOS_EXTRAS = {"inicio_atividade"}  # não são campos do cliente; vão para outros módulos


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
    if nome_arquivo.lower().endswith(".xls"):
        import xlrd
        sh = xlrd.open_workbook(file_contents=conteudo).sheet_by_index(0)
        return [[c.value for c in sh.row(r)] for r in range(sh.nrows)]
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


# ------------------------------------------------------------------ relatório de fichas do Domínio
FICHA = {  # rótulo da ficha -> cabeçalho equivalente
    "Razão social:": "Razão social", "Nome fantasia:": "Nome fantasia", "CNPJ/CPF/CEI/CAEPF:": "CNPJ/CPF",
    "E-mail:": "E-mail", "Telefone:": "Telefone", "Insc. estadual:": "Inscrição estadual",
    "Insc. municipal:": "Inscrição municipal", "Endereço:": "Endereço", "Número:": "Número", "Bairro:": "Bairro",
    "Município:": "Município", "UF:": "UF", "CEP:": "CEP", "Responsável Legal:": "Contato",
    "Início atividades:": "Início atividades",
}
FICHA_OBS = ["Código:", "Natureza Jurídica:", "CNAE 2.3:", "Ramo de atividade:", "Contador:", "Cliente desde:",
             "Complemento:", "Módulos:"]


def _texto_celula(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v or "").strip()


def fichas_para_tabela(linhas):
    """Converte o relatório de fichas (rótulo: valor) em cabeçalho + linhas. None se não for esse formato."""
    rotulos_col0 = [_texto_celula(l[0]) if l else "" for l in linhas]
    if rotulos_col0.count("Código:") < 1 or "Razão social:" not in rotulos_col0:
        return None
    fichas, atual = [], None
    for linha in linhas:
        cel = [_texto_celula(c) for c in linha]
        for i, c in enumerate(cel):
            if not c.endswith(":") or len(c) > 40:
                continue
            valor = ""
            for j in range(i + 1, len(cel)):
                if cel[j].endswith(":") and len(cel[j]) <= 40:
                    break
                if cel[j]:
                    valor = cel[j]
                    break
            if c == "Código:" and i == 0:
                atual = {}
                fichas.append(atual)
            if atual is not None and c not in atual:
                atual[c] = valor
    cab = list(FICHA.values()) + ["Observações"]
    tabela = [cab]
    for f in fichas:
        obs = "; ".join(f"{k.rstrip(':')} {f[k]}" for k in FICHA_OBS if f.get(k))
        tabela.append([f.get(k, "") for k in FICHA] + ["Importado do Domínio — " + obs])
    return tabela


def analisar(nome_arquivo, conteudo):
    """Lê a planilha e devolve (linhas_analisadas, mapa, erro_geral)."""
    linhas = ler_linhas(nome_arquivo, conteudo)
    linhas = fichas_para_tabela(linhas) or linhas
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
        if dados.get("cep"):
            dados["cep"] = so_digitos(dados["cep"])[:8]
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
            if len(doc) == 14 and not dados.get("regime"):
                motivo = "regime não informado — complete depois"
        vistos.add(doc)
        resultado.append({"linha": num, "dados": dados, "situacao": situacao, "motivo": motivo})
    return resultado, mapa, None


CAMPOS_TEXTO = [c for c in ALIASES if c != "documento" and c not in CAMPOS_EXTRAS]


def _data_br(texto):
    from datetime import date
    try:
        dia, mes, ano = str(texto).strip()[:10].split("/")
        return date(int(ano), int(mes), int(dia))
    except (ValueError, AttributeError):
        return None


def _extras(cliente, dados):
    inicio = _data_br(dados.get("inicio_atividade"))
    if inicio:
        from fiscal.models import PerfilFiscal
        pf, _ = PerfilFiscal.objects.get_or_create(cliente=cliente)
        if not pf.data_inicio_atividade:
            pf.data_inicio_atividade = inicio
            pf.save(update_fields=["data_inicio_atividade"])


def _cortar(campo, valor):
    limite = Cliente._meta.get_field(campo).max_length
    return valor[:limite] if limite else valor


def importar(linhas, responsavel=None):
    criados = atualizados = 0
    for item in linhas:
        d = item["dados"]
        if item["situacao"] == "novo":
            campos = {c: _cortar(c, d[c]) for c in CAMPOS_TEXTO if d.get(c)}
            # sem regime na planilha: "Outro" até alguém completar (nunca supõe Simples)
            campos.setdefault("regime", "OUTRO" if len(d["documento"]) == 14 else "PF")
            cli = Cliente.objects.create(documento=d["documento"], responsavel=responsavel, **campos)
            _extras(cli, d)
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
            _extras(cli, d)
    return criados, atualizados
