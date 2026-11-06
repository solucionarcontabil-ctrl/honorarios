from django.contrib import admin

from .models import Conta, HistoricoPadrao, LogContabil


@admin.register(Conta)
class ContaAdmin(admin.ModelAdmin):
    list_display = ["cliente", "codigo", "reduzido", "nome", "natureza", "sintetica", "papel"]
    list_filter = ["cliente", "natureza"]
    search_fields = ["codigo", "nome"]


@admin.register(LogContabil)
class LogAdmin(admin.ModelAdmin):
    list_display = ["em", "cliente", "usuario", "acao", "objeto"]
    list_filter = ["acao"]

    def has_change_permission(self, request, obj=None):
        return False  # trilha de auditoria não é editável

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(HistoricoPadrao)
# Lançamentos não aparecem no admin de propósito: toda mudança passa pelas regras e pela auditoria.
