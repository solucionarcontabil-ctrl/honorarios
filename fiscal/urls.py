from django.urls import path

from . import views as v

urlpatterns = [
    path("fiscal/", v.inicio, name="fiscal"),
    path("fiscal/tabelas/", v.tabelas, name="fiscal_tabelas"),
    path("fiscal/<int:cid>/", v.cliente, name="fiscal_cliente"),
    path("fiscal/<int:cid>/importar/", v.importar_xml, name="fiscal_importar"),
    path("fiscal/<int:cid>/simples/", v.simples, name="fiscal_simples"),
    path("fiscal/<int:cid>/presumido/", v.presumido, name="fiscal_presumido"),
    path("fiscal/<int:cid>/cfop/", v.cfop, name="fiscal_cfop"),
]
