from django.contrib import admin

from .models import Empregado, Folha, TabelaLegal

admin.site.register(TabelaLegal)


@admin.register(Empregado)
class EmpregadoAdmin(admin.ModelAdmin):
    list_display = ["cliente", "matricula", "nome", "tipo", "cargo", "salario", "ativo"]
    list_filter = ["cliente", "tipo", "ativo"]


@admin.register(Folha)
class FolhaAdmin(admin.ModelAdmin):
    list_display = ["cliente", "competencia", "tipo", "fechada"]
