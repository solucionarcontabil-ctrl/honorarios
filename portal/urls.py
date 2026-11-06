from django.urls import path

from . import views_cliente as c
from . import views_equipe as e

urlpatterns = [
    # equipe
    path("documentos/", e.documentos, name="documentos"),
    path("documentos/publicar/", e.publicar, name="documentos_publicar"),
    path("documentos/<int:pk>/baixar/", e.baixar, name="documento_baixar"),
    path("documentos/<int:pk>/processar/", e.processar, name="documento_processar"),
    path("documentos/<int:pk>/excluir/", e.excluir_arquivo, name="documento_excluir"),
    path("protocolos/", e.protocolos, name="protocolos"),
    path("protocolos/novo/", e.protocolo_novo, name="protocolo_novo"),
    path("protocolos/<int:pk>/", e.protocolo_detalhe, name="protocolo_detalhe"),
    path("protocolos/<int:pk>/confirmar/", e.protocolo_confirmar, name="protocolo_confirmar"),
    path("avisos/", e.avisos_email, name="avisos_email"),
    path("avisos/teste/", e.aviso_acao, {"acao": "teste"}, name="aviso_teste"),
    path("avisos/<int:pk>/", e.aviso_detalhe, name="aviso_detalhe"),
    path("avisos/<int:pk>/reenviar/", e.aviso_acao, {"acao": "reenviar"}, name="aviso_reenviar"),
    path("acessos/", e.acessos, name="acessos"),
    path("acessos/novo/", e.acesso_novo, name="acesso_novo"),
    path("acessos/<int:pk>/<str:acao>/", e.acesso_acao, name="acesso_acao"),
    # portal do cliente
    path("portal/", c.inicio, name="portal_inicio"),
    path("portal/empresa/<int:cliente_id>/", c.trocar_empresa, name="portal_empresa"),
    path("portal/documentos/", c.documentos, name="portal_documentos"),
    path("portal/documentos/<int:pk>/", c.baixar, name="portal_baixar"),
    path("portal/enviar/", c.enviar, name="portal_enviar"),
    path("portal/honorarios/", c.honorarios, name="portal_honorarios"),
    path("portal/honorarios/<int:pk>/recibo/", c.recibo, name="portal_recibo"),
    path("portal/protocolos/", c.protocolos, name="portal_protocolos"),
    path("portal/protocolos/<int:pk>/", c.protocolo, name="portal_protocolo"),
    path("portal/protocolos/<int:pk>/confirmar/", c.protocolo_confirmar, name="portal_protocolo_confirmar"),
    path("portal/senha/", c.senha, name="portal_senha"),
]
