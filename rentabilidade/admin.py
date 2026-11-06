from django.contrib import admin

from .models import Apontamento, Membro

admin.site.register(Membro)


@admin.register(Apontamento)
class ApontamentoAdmin(admin.ModelAdmin):
    list_display = ["data", "usuario", "cliente", "minutos", "atividade", "descricao"]
    list_filter = ["usuario", "atividade"]
