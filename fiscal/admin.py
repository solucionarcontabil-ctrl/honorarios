from django.contrib import admin

from .models import Apuracao, PerfilFiscal, ReceitaMensal

admin.site.register(PerfilFiscal)
admin.site.register(ReceitaMensal)


@admin.register(Apuracao)
class ApuracaoAdmin(admin.ModelAdmin):
    list_display = ["cliente", "tipo", "competencia", "total", "conferida"]
    list_filter = ["tipo", "conferida"]
