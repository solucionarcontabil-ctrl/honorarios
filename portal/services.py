import os
import secrets
import string

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import EXTENSOES_PERMITIDAS, TAMANHO_MAXIMO, AcessoPortal, Arquivo, ItemProtocolo, LogPortal, Protocolo


class ArquivoRecusado(Exception):
    pass


def ip_de(request):
    # atrás do Caddy o IP real vem no X-Forwarded-For (primeiro da lista)
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")) or None


def acesso_de(usuario):
    if not usuario.is_authenticated:
        return None
    try:
        return usuario.acesso_portal
    except AcessoPortal.DoesNotExist:
        return None


def log(request, acao, cliente=None, detalhe=""):
    LogPortal.objects.create(usuario=request.user if request.user.is_authenticated else None,
                             cliente=cliente, acao=acao, detalhe=detalhe[:250], ip=ip_de(request))


def validar_arquivo(f):
    ext = os.path.splitext(f.name)[1].lower()
    if ext not in EXTENSOES_PERMITIDAS:
        raise ArquivoRecusado(f"{f.name}: tipo de arquivo não permitido ({ext or 'sem extensão'}).")
    if f.size > TAMANHO_MAXIMO:
        raise ArquivoRecusado(f"{f.name}: maior que {TAMANHO_MAXIMO // 1048576} MB.")
    if f.size == 0:
        raise ArquivoRecusado(f"{f.name}: arquivo vazio.")


def _mes_texto(d):
    return d.strftime("%m/%Y") if d else ""


@transaction.atomic
def publicar(cliente, arquivos, usuario, categoria, titulo="", competencia=None, vencimento=None, valor=None,
             observacao=""):
    """Escritório envia documentos ao cliente pelo portal. Cria um protocolo de entrega."""
    for f in arquivos:
        validar_arquivo(f)
    prot = Protocolo.objects.create(cliente=cliente, tipo=Protocolo.ENTREGA, meio="PORTAL", responsavel=usuario,
                                    criado_por=usuario, observacoes=observacao)
    criados = []
    for f in arquivos:
        nome_titulo = titulo if (titulo and len(arquivos) == 1) else (
            f"{titulo} — {os.path.splitext(f.name)[0]}" if titulo else os.path.splitext(f.name)[0])
        a = Arquivo.objects.create(
            cliente=cliente, direcao=Arquivo.ENVIADO, categoria=categoria, titulo=nome_titulo[:200],
            competencia=competencia, vencimento=vencimento, valor=valor if len(arquivos) == 1 else None,
            arquivo=f, nome_original=f.name[:200], tamanho=f.size, observacao=observacao,
            enviado_por=usuario, protocolo=prot,
        )
        ItemProtocolo.objects.create(protocolo=prot, descricao=a.titulo, competencia=_mes_texto(competencia), arquivo=a)
        criados.append(a)
    return prot, criados


@transaction.atomic
def receber(cliente, arquivos, usuario, categoria, competencia=None, observacao="", meio="PORTAL", ip=None):
    """Cliente envia documentos ao escritório. O protocolo já nasce confirmado (quem enviou foi o cliente)."""
    for f in arquivos:
        validar_arquivo(f)
    acesso = acesso_de(usuario)
    prot = Protocolo.objects.create(
        cliente=cliente, tipo=Protocolo.RECEBIMENTO, meio=meio, responsavel=cliente.responsavel,
        contato_nome=acesso.nome if acesso else "", criado_por=usuario, observacoes=observacao,
    )
    prot.confirmar("ENVIO", usuario, ip)
    criados = []
    for f in arquivos:
        a = Arquivo.objects.create(
            cliente=cliente, direcao=Arquivo.RECEBIDO, categoria=categoria, titulo=os.path.splitext(f.name)[0][:200],
            competencia=competencia, arquivo=f, nome_original=f.name[:200], tamanho=f.size, observacao=observacao,
            enviado_por=usuario, protocolo=prot,
        )
        ItemProtocolo.objects.create(protocolo=prot, descricao=a.titulo, competencia=_mes_texto(competencia), arquivo=a)
        criados.append(a)
    # XMLs e ZIPs de notas vão direto para a Central de XMLs
    xmls = [a for a in criados if a.extensao in ("xml", "zip") and categoria == "NOTAS"]
    lote = None
    if xmls:
        from xmls.services import importar
        conteudos = []
        for a in xmls:
            a.arquivo.open("rb")
            conteudos.append((a.nome_original, a.arquivo.read()))
            a.arquivo.close()
        lote = importar(conteudos, usuario)
    return prot, criados, lote


def registrar_download(arquivo, request):
    """Primeiro download pelo cliente = ciência do documento (e do protocolo)."""
    if arquivo.direcao != Arquivo.ENVIADO or not acesso_de(request.user):
        return
    if not arquivo.visualizado_em:
        arquivo.visualizado_em = timezone.now()
        arquivo.visualizado_por = request.user
        arquivo.save(update_fields=["visualizado_em", "visualizado_por"])
    if arquivo.protocolo and not arquivo.protocolo.confirmado:
        arquivo.protocolo.confirmar("DOWNLOAD", request.user, ip_de(request))
    log(request, "download", arquivo.cliente, arquivo.titulo)


def gerar_senha(tamanho=10):
    alfabeto = string.ascii_letters + string.digits
    alfabeto = "".join(c for c in alfabeto if c not in "Il1O0o")  # evita confusão ao digitar
    return "".join(secrets.choice(alfabeto) for _ in range(tamanho))


@transaction.atomic
def criar_acesso(nome, login, clientes, criado_por, email="", telefone=""):
    User = get_user_model()
    senha = gerar_senha()
    usuario = User.objects.create_user(username=login, email=email, password=senha, first_name=nome[:150])
    acesso = AcessoPortal.objects.create(usuario=usuario, nome=nome, telefone=telefone, criado_por=criado_por)
    acesso.clientes.set(clientes)
    return acesso, senha


def nova_senha(acesso):
    senha = gerar_senha()
    acesso.usuario.set_password(senha)
    acesso.usuario.save(update_fields=["password"])
    return senha
