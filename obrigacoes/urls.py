from django.urls import path

from . import views

urlpatterns = [
    path("obrigacoes/", views.entregas, name="obrig_entregas"),
    path("obrigacoes/gerar/", views.gerar, name="obrig_gerar"),
    path("obrigacoes/lote/", views.entregas_lote, name="obrig_lote"),
    path("obrigacoes/catalogo/", views.catalogo, name="obrig_catalogo"),
    path("obrigacoes/catalogo/nova/", views.obrigacao_form, name="obrigacao_nova"),
    path("obrigacoes/catalogo/<int:pk>/", views.obrigacao_form, name="obrigacao_editar"),
    path("obrigacoes/feriados/novo/", views.feriado_form, name="feriado_novo"),
    path("obrigacoes/<int:pk>/editar/", views.entrega_editar, name="entrega_editar"),
    path("obrigacoes/<int:pk>/<str:acao>/", views.entrega_acao, name="entrega_acao"),
    path("tarefas/nova/", views.tarefa_nova, name="tarefa_nova"),
    path("clientes/<int:cliente_id>/obrigacoes/", views.cliente_obrigacoes, name="cliente_obrigacoes"),
    path("clientes/importar/", views.importar_clientes, name="importar_clientes"),
    path("certificados/", views.certificados, name="certificados"),
    path("certificados/novo/", views.certificado_form, name="certificado_novo"),
    path("certificados/ler/", views.certificado_ler_pfx, name="certificado_ler"),
    path("certificados/<int:pk>/", views.certificado_form, name="certificado_editar"),
]
