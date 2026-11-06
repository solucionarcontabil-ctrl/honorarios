"""Avisos automáticos por e-mail do portal.

Todo envio passa por `_enviar`, que grava um AvisoEmail (com chave única para não
repetir), manda texto + HTML e nunca derruba a operação principal se o e-mail falhar.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from cobranca.equipe import equipe
from cobranca.models import Configuracao

from .models import AcessoPortal, Arquivo, AvisoEmail

log = logging.getLogger(__name__)


def envio_configurado():
    """Falso enquanto o .env não tiver conta de envio (o padrão só escreve no log)."""
    return not settings.EMAIL_BACKEND.endswith("console.EmailBackend")


def _remetente(config):
    de = settings.DEFAULT_FROM_EMAIL
    return de if "<" in de else f"{config.nome_escritorio} <{de}>"


def _destinatarios_cliente(cliente, preferencias=("IMEDIATO",)):
    acessos = AcessoPortal.objects.filter(clientes=cliente, ativo=True, avisos__in=preferencias).select_related("usuario")
    return sorted({a.usuario.email.strip().lower() for a in acessos if a.usuario.email})


def _contexto(config, **extra):
    return {"config": config, "site": settings.SITE_URL.rstrip("/"), **extra}


def _montar(modelo, contexto):
    texto = render_to_string(f"emails/{modelo}.txt", contexto).strip()
    html = render_to_string(f"emails/{modelo}.html", contexto)
    return texto, html


def _enviar(tipo, chave, assunto, para, texto, html, cliente=None, config=None):
    """Grava e envia. Retorna o AvisoEmail (ou None se esse aviso já tinha sido criado antes)."""
    config = config or Configuracao.atual()
    try:
        with transaction.atomic():
            aviso = AvisoEmail.objects.create(
                tipo=tipo, chave=chave[:80], cliente=cliente, destinatarios=", ".join(para),
                assunto=assunto[:200], corpo=texto, corpo_html=html,
                status="ENVIADO" if para else "SEM_DESTINO",
            )
    except IntegrityError:
        return None  # já avisado
    if para:
        despachar(aviso, config)
    return aviso


def despachar(aviso, config=None):
    """(Re)tenta mandar um aviso gravado."""
    config = config or Configuracao.atual()
    para = [e.strip() for e in aviso.destinatarios.split(",") if e.strip()]
    if not para:
        return False
    if not envio_configurado():
        aviso.status, aviso.erro = "SIMULADO", ""
        aviso.save(update_fields=["status", "erro"])
        return False
    aviso.tentativas += 1
    try:
        msg = EmailMultiAlternatives(aviso.assunto, aviso.corpo, _remetente(config), para,
                                     reply_to=[config.email] if config.email else None)
        if aviso.corpo_html:
            msg.attach_alternative(aviso.corpo_html, "text/html")
        msg.send()
        aviso.status, aviso.erro, aviso.enviado_em = "ENVIADO", "", timezone.now()
        ok = True
    except Exception as e:  # SMTP fora do ar, senha errada, etc.
        log.warning("Falha ao enviar aviso %s: %s", aviso.chave, e)
        aviso.status, aviso.erro = "ERRO", str(e)[:1000]
        ok = False
    aviso.save(update_fields=["status", "erro", "enviado_em", "tentativas"])
    return ok


# ---------------------------------------------------------------- gatilhos
def documentos_publicados(protocolo):
    """Chamado ao publicar: um e-mail por protocolo para quem quer aviso imediato."""
    config = Configuracao.atual()
    if not config.avisos_portal_ativos:
        return None
    cliente = protocolo.cliente
    arquivos = list(protocolo.arquivos.all())
    if not arquivos:
        return None
    ctx = _contexto(config, cliente=cliente, arquivos=arquivos, protocolo=protocolo)
    texto, html = _montar("documentos", ctx)
    qtd = len(arquivos)
    assunto = (f"Novo documento: {arquivos[0].titulo}" if qtd == 1 else f"{qtd} novos documentos") + f" — {config.nome_escritorio}"
    return _enviar("DOCUMENTOS", f"doc-{protocolo.pk}", assunto, _destinatarios_cliente(cliente), texto, html, cliente, config)


def documentos_recebidos(protocolo):
    """Cliente mandou arquivos: avisa o responsável (ou toda a equipe, se não houver)."""
    config = Configuracao.atual()
    if not config.avisos_portal_ativos:
        return None
    cliente = protocolo.cliente
    resp = cliente.responsavel
    para = [resp.email] if resp and resp.email else [u.email for u in equipe() if u.email]
    ctx = _contexto(config, cliente=cliente, arquivos=list(protocolo.arquivos.all()), protocolo=protocolo)
    texto, html = _montar("recebidos", ctx)
    assunto = f"{cliente} enviou {protocolo.arquivos.count()} documento(s) pelo portal"
    return _enviar("RECEBIDOS", f"rec-{protocolo.pk}", assunto, sorted(set(para)), texto, html, cliente, config)


def resumo_diario(hoje=None):
    """Para quem escolheu 'um resumo por dia': documentos publicados nas últimas 24h e ainda não baixados."""
    config = Configuracao.atual()
    if not config.avisos_portal_ativos:
        return 0
    hoje = hoje or timezone.localdate()
    desde = timezone.now() - timedelta(days=1)
    enviados = 0
    for acesso in AcessoPortal.objects.filter(ativo=True, avisos="RESUMO").prefetch_related("clientes").select_related("usuario"):
        if not acesso.usuario.email:
            continue
        grupos = []
        for cliente in acesso.clientes.filter(ativo=True):
            docs = list(cliente.arquivos.filter(direcao=Arquivo.ENVIADO, enviado_em__gte=desde, visualizado_em__isnull=True))
            if docs:
                grupos.append((cliente, docs))
        if not grupos:
            continue
        texto, html = _montar("resumo", _contexto(config, acesso=acesso, grupos=grupos))
        total = sum(len(d) for _, d in grupos)
        aviso = _enviar("RESUMO", f"resumo-{acesso.pk}-{hoje:%Y%m%d}", f"Resumo: {total} documento(s) novo(s) — {config.nome_escritorio}",
                        [acesso.usuario.email], texto, html, grupos[0][0], config)
        enviados += bool(aviso and aviso.status == "ENVIADO")
    return enviados


def lembrar_guias(hoje=None):
    """Guias que vencem em X dias e ninguém da empresa baixou ainda."""
    config = Configuracao.atual()
    if not config.avisos_portal_ativos or not config.lembrete_guia_dias:
        return 0
    hoje = hoje or timezone.localdate()
    alvo = hoje + timedelta(days=config.lembrete_guia_dias)
    guias = (Arquivo.objects.filter(direcao=Arquivo.ENVIADO, categoria="GUIA", vencimento__gte=hoje, vencimento__lte=alvo,
                                    visualizado_em__isnull=True).select_related("cliente"))
    enviados = 0
    por_cliente = {}
    for g in guias:
        por_cliente.setdefault(g.cliente, []).append(g)
    for cliente, lista in por_cliente.items():
        para = _destinatarios_cliente(cliente, ("IMEDIATO", "RESUMO"))
        texto, html = _montar("guias", _contexto(config, cliente=cliente, guias=lista))
        chave = f"guia-{cliente.pk}-{hoje:%Y%m%d}-" + "-".join(str(g.pk) for g in lista)[:40]
        aviso = _enviar("GUIA", chave, f"Guia vencendo sem download — {cliente} — {config.nome_escritorio}",
                        para, texto, html, cliente, config)
        enviados += bool(aviso and aviso.status == "ENVIADO")
    return enviados


def email_teste(para, config=None):
    config = config or Configuracao.atual()
    texto, html = _montar("teste", _contexto(config))
    chave = f"teste-{timezone.now():%Y%m%d%H%M%S%f}"
    return _enviar("TESTE", chave, f"Teste de e-mail — {config.nome_escritorio}", [para], texto, html, None, config)


def reenviar_com_erro(limite=50):
    """Tenta de novo os avisos que deram erro nas últimas 48h."""
    desde = timezone.now() - timedelta(hours=48)
    ok = 0
    for aviso in AvisoEmail.objects.filter(status="ERRO", criado_em__gte=desde, tentativas__lt=5)[:limite]:
        ok += despachar(aviso)
    return ok
