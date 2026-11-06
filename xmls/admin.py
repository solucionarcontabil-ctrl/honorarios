from django.contrib import admin

from .models import Documento, Evento, Lote


@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    list_display = ["modelo", "numero", "emissao", "emit_nome", "dest_nome", "valor_total", "situacao"]
    list_filter = ["modelo", "situacao", "competencia"]
    search_fields = ["chave", "numero", "emit_nome", "dest_nome", "emit_doc", "dest_doc"]


admin.site.register(Evento)
admin.site.register(Lote)
