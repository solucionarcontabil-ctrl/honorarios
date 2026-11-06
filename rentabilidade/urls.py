from django.urls import path

from . import views

urlpatterns = [
    path("horas/", views.horas, name="horas"),
    path("horas/lancar/", views.lancar, name="horas_lancar"),
    path("horas/cronometro/<str:acao>/", views.cronometro, name="horas_cronometro"),
    path("horas/<int:pk>/excluir/", views.excluir, name="horas_excluir"),
    path("rentabilidade/", views.relatorio, name="rentabilidade"),
    path("rentabilidade/cliente/<int:cliente_id>/", views.cliente, name="rentabilidade_cliente"),
    path("equipe/", views.equipe_custos, name="equipe_custos"),
]
