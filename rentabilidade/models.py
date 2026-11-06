import math
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from cobranca.models import Cliente

ATIVIDADES = [
    ("CONTABIL", "Contábil"), ("FISCAL", "Fiscal"), ("FOLHA", "Folha / DP"), ("SOCIETARIO", "Societário / legalização"),
    ("ATENDIMENTO", "Atendimento / reunião"), ("IRPF", "IRPF"), ("ADMIN", "Administrativo do escritório"),
    ("OUTROS", "Outros"),
]


class Membro(models.Model):
    """Custo da hora de cada pessoa da equipe."""

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="membro")
    custo_hora = models.DecimalField(
        "custo da hora (R$)", max_digits=8, decimal_places=2, default=Decimal("0"),
        help_text="Inclua salário/pró-labore, encargos e a parte da estrutura do escritório "
                  "(aluguel, sistemas, energia) dividido pelas horas produtivas do mês.",
    )

    def __str__(self):
        return f"{self.usuario} — R$ {self.custo_hora}/h"


class Apontamento(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="apontamentos")
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="apontamentos", null=True, blank=True,
                                help_text="Vazio = trabalho interno do escritório.")
    data = models.DateField(default=timezone.localdate, db_index=True)
    minutos = models.PositiveIntegerField(default=0)
    atividade = models.CharField(max_length=12, choices=ATIVIDADES, default="CONTABIL")
    descricao = models.CharField("descrição", max_length=200, blank=True)
    entrega = models.ForeignKey("obrigacoes.Entrega", on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="apontamentos")
    inicio = models.DateTimeField(null=True, blank=True)
    em_andamento = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data", "-criado_em"]

    def __str__(self):
        return f"{self.usuario} {self.data:%d/%m} {self.horas_texto} {self.cliente or 'interno'}"

    @property
    def horas_texto(self):
        m = self.minutos_correntes
        return f"{m // 60}h{m % 60:02d}"

    @property
    def minutos_correntes(self):
        if self.em_andamento and self.inicio:
            return max(int((timezone.now() - self.inicio).total_seconds() // 60), 0)
        return self.minutos

    def parar(self):
        if not self.em_andamento:
            return
        segundos = (timezone.now() - self.inicio).total_seconds()
        self.minutos = max(math.ceil(segundos / 60), 1)
        self.em_andamento = False
        self.save(update_fields=["minutos", "em_andamento"])
