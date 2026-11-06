from django.urls import path

from . import views as v

urlpatterns = [
    path("folha/", v.inicio, name="folha"),
    path("folha/tabelas/", v.tabelas, name="folha_tabelas"),
    path("folha/<int:cid>/", v.cliente, name="folha_cliente"),
    path("folha/<int:cid>/configuracao/", v.config, name="folha_config"),
    path("folha/<int:cid>/empregado/novo/", v.empregado, name="folha_empregado_novo"),
    path("folha/<int:cid>/empregado/<int:pk>/", v.empregado, name="folha_empregado"),
    path("folha/<int:cid>/variaveis/", v.variaveis, name="folha_variaveis"),
    path("folha/<int:cid>/calcular/", v.calcular, name="folha_calcular"),
    path("folha/<int:cid>/ferias/nova/", v.ferias, name="folha_ferias_nova"),
    path("folha/<int:cid>/ferias/<int:pk>/", v.ferias, name="folha_ferias"),
    path("folha/<int:cid>/rescisao/<int:emp_id>/", v.rescisao, name="folha_rescisao"),
    path("folha/processamento/<int:pk>/", v.detalhe, name="folha_detalhe"),
    path("folha/processamento/<int:pk>/holerites.pdf", v.holerites_pdf, name="folha_pdf"),
    path("folha/processamento/<int:pk>/<str:acao>/", v.acao, name="folha_acao"),
]
