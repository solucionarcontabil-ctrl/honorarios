from django.conf import settings
from django.db import models

from cobranca.models import Cliente

from .tabelas import TIPOS_PRESUMIDO, TIPOS_RECEITA


class PerfilFiscal(models.Model):
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, related_name="perfil_fiscal")
    data_inicio_atividade = models.DateField(
        "início das atividades", null=True, blank=True,
        help_text="Usado no Simples para calcular a RBT12 proporcional nos primeiros 12 meses.")
    anexo_mercadorias = models.CharField("receita de mercadorias (NF-e/NFC-e) vai para", max_length=12,
                                         choices=[("SN_I", "Anexo I (comércio)"), ("SN_II", "Anexo II (indústria)")],
                                         default="SN_I")
    anexo_servicos = models.CharField("receita de serviços (NFS-e) vai para", max_length=12,
                                      choices=[("SN_III", "Anexo III"), ("SN_IV", "Anexo IV"), ("SN_V", "Anexo V"),
                                               ("SN_FATOR_R", "Anexo III ou V pelo fator R")], default="SN_III")
    atividade_mercadorias = models.CharField("presumido: atividade das vendas", max_length=30,
                                             choices=TIPOS_PRESUMIDO, default="LP_COMERCIO")
    atividade_servicos = models.CharField("presumido: atividade dos serviços", max_length=30,
                                          choices=TIPOS_PRESUMIDO, default="LP_SERVICOS")
    presuncao_irpj = models.DecimalField("presunção IRPJ própria (%)", max_digits=5, decimal_places=2, null=True,
                                         blank=True, help_text="Vazio = percentual legal da atividade.")
    presuncao_csll = models.DecimalField("presunção CSLL própria (%)", max_digits=5, decimal_places=2, null=True, blank=True)
    aliquota_iss = models.DecimalField("alíquota de ISS (%)", max_digits=5, decimal_places=2, default=5,
                                       help_text="Presumido: ISS próprio sobre serviços. Confira a lei do município.")
    observacoes = models.TextField("observações fiscais", blank=True)

    class Meta:
        verbose_name = "perfil fiscal"


class ReceitaMensal(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="receitas_fiscais")
    competencia = models.DateField(db_index=True)
    tipo = models.CharField(max_length=30, choices=TIPOS_RECEITA)
    valor = models.DecimalField(max_digits=15, decimal_places=2)
    origem = models.CharField(max_length=6, choices=[("XML", "Notas fiscais"), ("MANUAL", "Digitado")], default="MANUAL")
    alterado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    alterado_em = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("cliente", "competencia", "tipo")]
        ordering = ["competencia", "tipo"]


class FolhaMensal(models.Model):
    """Folha de salários do mês para o fator R (salários, pró-labore, 13º, FGTS e CPP)."""

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="folhas_fiscais")
    competencia = models.DateField()
    valor = models.DecimalField(max_digits=15, decimal_places=2)

    class Meta:
        unique_together = [("cliente", "competencia")]


class Apuracao(models.Model):
    TIPOS = [("SIMPLES", "Simples Nacional"), ("LP_MENSAL", "Presumido — PIS/COFINS/ISS"),
             ("LP_TRIMESTRAL", "Presumido — IRPJ/CSLL")]
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="apuracoes")
    tipo = models.CharField(max_length=13, choices=TIPOS)
    competencia = models.DateField(help_text="Mês (ou 1º mês do trimestre).")
    total = models.DecimalField(max_digits=15, decimal_places=2)
    memoria = models.JSONField(default=dict)
    conferida = models.BooleanField(default=False)
    conferida_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="+")
    conferida_em = models.DateTimeField(null=True, blank=True)
    calculada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    calculada_em = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("cliente", "tipo", "competencia")]
        ordering = ["-competencia"]
        verbose_name = "apuração"
