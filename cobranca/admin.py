from django.contrib import admin

from .models import Cliente, Cobranca, Configuracao, Contrato, HistoricoReajuste, LembreteEnviado


class ContratoInline(admin.TabularInline):
    model = Contrato
    extra = 0


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ["nome", "documento", "regime", "responsavel", "ativo"]
    list_filter = ["ativo", "regime", "responsavel"]
    search_fields = ["nome", "nome_fantasia", "documento"]
    inlines = [ContratoInline]


@admin.register(Cobranca)
class CobrancaAdmin(admin.ModelAdmin):
    list_display = ["cliente", "competencia", "tipo", "valor", "vencimento", "status", "data_pagamento"]
    list_filter = ["status", "tipo", "competencia"]
    search_fields = ["cliente__nome", "descricao", "gateway_id"]
    date_hierarchy = "vencimento"


admin.site.register(Configuracao)
admin.site.register(HistoricoReajuste)
admin.site.register(LembreteEnviado)
admin.site.site_header = "Honorários — administração"
