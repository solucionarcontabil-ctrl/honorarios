from django.contrib import admin

from .models import AcessoPortal, Arquivo, ItemProtocolo, LogPortal, Protocolo


class ItemInline(admin.TabularInline):
    model = ItemProtocolo
    extra = 0


@admin.register(Protocolo)
class ProtocoloAdmin(admin.ModelAdmin):
    list_display = ["codigo", "cliente", "tipo", "meio", "data", "confirmado_em"]
    list_filter = ["tipo", "meio"]
    inlines = [ItemInline]


@admin.register(Arquivo)
class ArquivoAdmin(admin.ModelAdmin):
    list_display = ["titulo", "cliente", "direcao", "categoria", "enviado_em", "visualizado_em"]
    list_filter = ["direcao", "categoria"]


admin.site.register(AcessoPortal)
admin.site.register(LogPortal)
