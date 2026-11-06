from django.contrib import admin

from .models import Certificado, ClienteObrigacao, Entrega, Feriado, Obrigacao


@admin.register(Obrigacao)
class ObrigacaoAdmin(admin.ModelAdmin):
    list_display = ["sigla", "nome", "periodicidade", "regra_prazo", "dia", "regimes", "ativa"]


@admin.register(Entrega)
class EntregaAdmin(admin.ModelAdmin):
    list_display = ["titulo", "cliente", "prazo", "responsavel", "status"]
    list_filter = ["status", "obrigacao", "responsavel"]
    search_fields = ["titulo", "cliente__nome"]
    date_hierarchy = "prazo"


admin.site.register(Certificado)
admin.site.register(ClienteObrigacao)
admin.site.register(Feriado)
