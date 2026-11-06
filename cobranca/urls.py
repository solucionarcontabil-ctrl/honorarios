from django.urls import path

from . import views

urlpatterns = [
    path("", views.painel, name="painel"),
    path("clientes/", views.clientes_lista, name="clientes"),
    path("clientes/novo/", views.cliente_form, name="cliente_novo"),
    path("clientes/completar/", views.completar_lote, name="clientes_completar"),
    path("clientes/<int:pk>/", views.cliente_detalhe, name="cliente_detalhe"),
    path("clientes/<int:pk>/editar/", views.cliente_form, name="cliente_editar"),
    path("clientes/<int:cliente_id>/honorario/novo/", views.contrato_form, name="contrato_novo"),
    path("honorarios/<int:pk>/editar/", views.contrato_form, name="contrato_editar"),
    path("cobrancas/", views.cobrancas_lista, name="cobrancas"),
    path("cobrancas/nova/", views.cobranca_nova, name="cobranca_nova"),
    path("cobrancas/emitir-lote/", views.cobrancas_emitir_lote, name="cobrancas_emitir_lote"),
    path("cobrancas/<int:pk>/", views.cobranca_detalhe, name="cobranca_detalhe"),
    path("cobrancas/<int:pk>/baixa/", views.cobranca_baixa, name="cobranca_baixa"),
    path("cobrancas/<int:pk>/recibo/", views.cobranca_recibo, name="cobranca_recibo"),
    path("cobrancas/<int:pk>/<str:acao>/", views.cobranca_acao, name="cobranca_acao"),
    path("gerar/", views.gerar, name="gerar"),
    path("inadimplencia/", views.inadimplencia, name="inadimplencia"),
    path("reajustes/", views.reajustes, name="reajustes"),
    path("configuracoes/", views.configuracao, name="configuracao"),
    path("webhooks/asaas/", views.webhook_asaas, name="webhook_asaas"),
]
