from django.urls import path

from . import views

urlpatterns = [
    path("xml/", views.central, name="xml_central"),
    path("xml/lote/<int:pk>/", views.lote, name="xml_lote"),
    path("xml/sem-cliente/", views.sem_cliente, name="xml_sem_cliente"),
    path("xml/revincular/", views.revincular, name="xml_revincular"),
    path("xml/nota/<int:pk>/", views.documento, name="xml_documento"),
    path("xml/cliente/<int:cliente_id>/", views.cliente_xmls, name="xml_cliente"),
    path("xml/cliente/<int:cliente_id>/faturamento/", views.faturamento, name="xml_faturamento"),
]
