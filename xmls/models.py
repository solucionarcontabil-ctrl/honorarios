import gzip

from django.conf import settings
from django.db import models

from cobranca.models import Cliente


class Lote(models.Model):
    """Um envio de arquivos (para saber quem enviou o quê e quando)."""

    enviado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    enviado_em = models.DateTimeField(auto_now_add=True)
    arquivos = models.PositiveIntegerField(default=0)
    novos = models.PositiveIntegerField(default=0)
    repetidos = models.PositiveIntegerField(default=0)
    eventos = models.PositiveIntegerField(default=0)
    sem_cliente = models.PositiveIntegerField(default=0)
    erros = models.PositiveIntegerField(default=0)
    detalhes_erros = models.TextField(blank=True)

    class Meta:
        ordering = ["-enviado_em"]


class Documento(models.Model):
    MODELOS = [("NFE", "NF-e"), ("NFCE", "NFC-e"), ("CTE", "CT-e"), ("NFSE", "NFS-e")]
    SITUACOES = [("AUTORIZADA", "Autorizada"), ("CANCELADA", "Cancelada"), ("DENEGADA", "Denegada"),
                 ("SEM_PROTOCOLO", "Sem protocolo")]
    OPERACOES = [("SAIDA", "Saída"), ("ENTRADA", "Entrada")]

    chave = models.CharField(max_length=60, unique=True)
    modelo = models.CharField(max_length=4, choices=MODELOS)
    numero = models.CharField(max_length=20, blank=True)
    serie = models.CharField(max_length=5, blank=True)
    emissao = models.DateField(null=True, db_index=True)
    competencia = models.DateField(null=True, db_index=True, help_text="Mês da emissão (dia 1).")

    emit_doc = models.CharField("CNPJ/CPF emitente", max_length=14, db_index=True)
    emit_nome = models.CharField("emitente", max_length=150, blank=True)
    emit_uf = models.CharField(max_length=2, blank=True)
    dest_doc = models.CharField("CNPJ/CPF destinatário/tomador", max_length=14, blank=True, db_index=True)
    dest_nome = models.CharField("destinatário/tomador", max_length=150, blank=True)

    cliente_emitente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, related_name="xmls_emitidos")
    cliente_destinatario = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, related_name="xmls_recebidos")

    tipo_operacao = models.CharField("operação (do emitente)", max_length=7, choices=OPERACOES, default="SAIDA")
    natureza = models.CharField("natureza da operação", max_length=120, blank=True)
    situacao = models.CharField(max_length=13, choices=SITUACOES, default="AUTORIZADA")

    valor_total = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    valor_icms = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    valor_icms_st = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    valor_ipi = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    valor_pis = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    valor_cofins = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    valor_iss = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    # Reforma Tributária (EC 132/2023, LC 214/2025): em 2026 o destaque é informativo/teste
    valor_ibs = models.DecimalField("IBS", max_digits=15, decimal_places=2, default=0)
    valor_cbs = models.DecimalField("CBS", max_digits=15, decimal_places=2, default=0)
    cfops = models.JSONField(default=dict, blank=True, help_text="{CFOP: valor dos produtos}")

    nome_arquivo = models.CharField(max_length=200, blank=True)
    xml_gz = models.BinaryField(editable=False)
    lote = models.ForeignKey(Lote, on_delete=models.SET_NULL, null=True, blank=True, related_name="documentos")
    importado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-emissao", "-numero"]

    def __str__(self):
        return f"{self.get_modelo_display()} {self.numero} — {self.emit_nome}"

    @property
    def xml(self):
        return gzip.decompress(bytes(self.xml_gz))

    @property
    def nome_download(self):
        return self.nome_arquivo if self.nome_arquivo.lower().endswith(".xml") else f"{self.chave}.xml"

    @property
    def lista_cfops(self):
        return ", ".join(sorted(self.cfops))


class Evento(models.Model):
    """Eventos (cancelamento, carta de correção...). Guardados mesmo se a nota ainda não chegou."""

    chave = models.CharField(max_length=60, db_index=True)
    tipo = models.CharField(max_length=6)
    descricao = models.CharField(max_length=100, blank=True)
    data = models.DateTimeField(null=True)
    xml_gz = models.BinaryField(editable=False)
    importado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("chave", "tipo", "data")]
        ordering = ["-data"]
