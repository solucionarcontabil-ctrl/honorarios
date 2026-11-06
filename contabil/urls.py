from django.urls import path

from . import views as v

urlpatterns = [
    path("contabil/", v.inicio, name="contabil"),
    path("contabil/<int:cid>/", v.cliente, name="contabil_cliente"),
    path("contabil/<int:cid>/plano/", v.plano, name="contabil_plano"),
    path("contabil/<int:cid>/plano/nova/", v.conta_form, name="contabil_conta_nova"),
    path("contabil/<int:cid>/plano/<int:pk>/", v.conta_form, name="contabil_conta"),
    path("contabil/<int:cid>/lancamentos/novo/", v.lancamento_form, name="contabil_lancamento_novo"),
    path("contabil/<int:cid>/lancamentos/<int:pk>/", v.lancamento_form, name="contabil_lancamento"),
    path("contabil/<int:cid>/lancamentos/<int:pk>/<str:acao>/", v.lancamento_acao, name="contabil_lancamento_acao"),
    path("contabil/<int:cid>/extrato/", v.extrato, name="contabil_extrato"),
    path("contabil/<int:cid>/extrato/regra/<int:pk>/excluir/", v.regra_excluir, name="contabil_regra_excluir"),
    path("contabil/<int:cid>/notas/", v.notas, name="contabil_notas"),
    path("contabil/<int:cid>/balancete/", v.balancete, name="contabil_balancete"),
    path("contabil/<int:cid>/razao/", v.razao, name="contabil_razao"),
    path("contabil/<int:cid>/diario/", v.diario, name="contabil_diario"),
    path("contabil/<int:cid>/dre/", v.dre, name="contabil_dre"),
    path("contabil/<int:cid>/balanco/", v.balanco, name="contabil_balanco"),
    path("contabil/<int:cid>/fechamento/", v.fechamento, name="contabil_fechamento"),
    path("contabil/<int:cid>/auditoria/", v.auditoria, name="contabil_auditoria"),
]
