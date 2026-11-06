"""Escrituração contábil por partidas dobradas.

Princípios (NBC ITG 2000 — Escrituração Contábil):
- ordem cronológica, sem rasuras: lançamento de período fechado NÃO é alterado nem
  excluído; a correção é feita por estorno (novo lançamento invertido);
- todo lançamento tem débitos = créditos e só usa contas analíticas;
- toda alteração fica registrada na trilha de auditoria (LogContabil).
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from cobranca.models import Cliente

NATUREZAS = [
    ("01", "Ativo"), ("02", "Passivo"), ("03", "Patrimônio líquido"), ("04", "Resultado"),
    ("05", "Compensação"), ("09", "Outras"),
]  # códigos de natureza do leiaute da ECD

GRUPOS_DRE = [
    ("", "—"),
    ("RECEITA_BRUTA", "Receita bruta"),
    ("DEDUCOES", "(-) Deduções da receita (impostos, devoluções)"),
    ("CUSTOS", "(-) Custos das mercadorias/serviços"),
    ("DESP_OPERACIONAIS", "(-) Despesas operacionais"),
    ("RECEITAS_FINANCEIRAS", "Receitas financeiras"),
    ("DESP_FINANCEIRAS", "(-) Despesas financeiras"),
    ("OUTRAS_RECEITAS", "Outras receitas"),
    ("OUTRAS_DESPESAS", "(-) Outras despesas"),
    ("IRPJ_CSLL", "(-) IRPJ e CSLL"),
]

GRUPOS_DEVEDORES = {"DEDUCOES", "CUSTOS", "DESP_OPERACIONAIS", "DESP_FINANCEIRAS", "OUTRAS_DESPESAS", "IRPJ_CSLL"}

# Papéis: contas que o sistema usa em rotinas automáticas (XML, OFX, encerramento)
PAPEIS = [
    ("", "—"),
    ("CAIXA", "Caixa"),
    ("BANCO", "Bancos (conta movimento padrão)"),
    ("CLIENTES", "Clientes / duplicatas a receber"),
    ("FORNECEDORES", "Fornecedores"),
    ("ESTOQUE", "Estoque de mercadorias"),
    ("RECEITA_VENDAS", "Receita de vendas de mercadorias"),
    ("RECEITA_SERVICOS", "Receita de prestação de serviços"),
    ("CUSTO_COMPRAS", "Compras / custo (quando não controla estoque)"),
    ("ARE", "Apuração do resultado do exercício"),
    ("LUCROS_ACUMULADOS", "Lucros acumulados"),
    ("PREJUIZOS_ACUMULADOS", "Prejuízos acumulados"),
    ("TARIFAS_BANCARIAS", "Despesas bancárias"),
    ("RENDIMENTOS", "Rendimentos de aplicações"),
]


class Conta(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="contas")
    codigo = models.CharField("código (classificação)", max_length=30, help_text="Ex.: 1.1.1.02.001")
    reduzido = models.PositiveIntegerField("código reduzido")
    nome = models.CharField(max_length=150)
    natureza = models.CharField(max_length=2, choices=NATUREZAS)
    sintetica = models.BooleanField("sintética (agrupadora)", default=False,
                                    help_text="Sintéticas só somam; lançamentos vão nas analíticas.")
    grupo_dre = models.CharField("grupo na DRE", max_length=20, choices=GRUPOS_DRE, blank=True)
    papel = models.CharField("uso automático", max_length=20, choices=PAPEIS, blank=True)
    codigo_referencial = models.CharField(
        "código no plano referencial (RFB)", max_length=30, blank=True,
        help_text="Mapeamento para o plano referencial da ECD/ECF.")
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ["codigo"]
        unique_together = [("cliente", "codigo"), ("cliente", "reduzido")]

    def __str__(self):
        return f"{self.codigo} {self.nome}"

    @property
    def nivel(self):
        return self.codigo.count(".") + 1

    @property
    def devedora(self):
        """Natureza do saldo: ativo e contas de resultado de custo/despesa são devedoras."""
        if self.natureza == "01":
            return True
        if self.natureza == "04":
            if self.grupo_dre:
                return self.grupo_dre in GRUPOS_DEVEDORES
            return self.codigo.startswith("4")  # no plano padrão: 3 = receitas, 4 = custos e despesas
        return False

    def clean(self):
        if self.papel and Conta.objects.filter(cliente=self.cliente, papel=self.papel).exclude(pk=self.pk).exists():
            raise ValidationError({"papel": "Já existe outra conta com esse uso automático neste cliente."})
        if self.papel and self.sintetica:
            raise ValidationError({"papel": "Conta sintética não recebe lançamentos."})


class HistoricoPadrao(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, null=True, blank=True,
                                help_text="Vazio = vale para todos os clientes.")
    codigo = models.PositiveIntegerField()
    texto = models.CharField(max_length=200)

    class Meta:
        ordering = ["codigo"]
        verbose_name = "histórico padrão"
        verbose_name_plural = "históricos padrão"

    def __str__(self):
        return f"{self.codigo} — {self.texto}"


class Fechamento(models.Model):
    """Período contábil fechado: nada com data <= fechado_ate pode ser criado/alterado/excluído."""

    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, related_name="fechamento_contabil")
    fechado_ate = models.DateField()
    fechado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    fechado_em = models.DateTimeField(auto_now=True)


class Lancamento(models.Model):
    ORIGENS = [("MANUAL", "Manual"), ("OFX", "Extrato bancário"), ("XML", "Notas fiscais"),
               ("ENCERRAMENTO", "Encerramento do exercício"), ("ESTORNO", "Estorno"), ("IMPORTACAO", "Importação")]

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="lancamentos")
    numero = models.PositiveIntegerField(editable=False)
    data = models.DateField(db_index=True)
    historico = models.CharField("histórico", max_length=300)
    documento = models.CharField("nº documento", max_length=60, blank=True)
    origem = models.CharField(max_length=12, choices=ORIGENS, default="MANUAL")
    origem_ref = models.CharField(max_length=80, blank=True, db_index=True,
                                  help_text="Identifica a origem (chave da nota, FITID do extrato) para não duplicar.")
    estorno_de = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True, related_name="estornado_por")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)
    alterado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    alterado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["data", "numero"]
        unique_together = [("cliente", "numero")]
        verbose_name = "lançamento"

    def __str__(self):
        return f"Lanç. {self.numero} {self.data:%d/%m/%Y} {self.historico[:40]}"

    @property
    def valor(self):
        return sum((p.valor for p in self.partidas.all() if p.tipo == "D"), Decimal("0"))


class Partida(models.Model):
    lancamento = models.ForeignKey(Lancamento, on_delete=models.CASCADE, related_name="partidas")
    conta = models.ForeignKey(Conta, on_delete=models.PROTECT, related_name="partidas")
    tipo = models.CharField(max_length=1, choices=[("D", "Débito"), ("C", "Crédito")])
    valor = models.DecimalField(max_digits=15, decimal_places=2)
    complemento = models.CharField("histórico complementar", max_length=200, blank=True)

    class Meta:
        ordering = ["lancamento", "-tipo", "id"]
        indexes = [models.Index(fields=["conta", "tipo"])]


class LogContabil(models.Model):
    """Trilha de auditoria: quem fez o quê, quando, de onde, com o antes e o depois."""

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="logs_contabeis")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    acao = models.CharField(max_length=30)
    objeto = models.CharField(max_length=80)
    antes = models.JSONField(null=True, blank=True)
    depois = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-em"]


class RegraExtrato(models.Model):
    """Se a descrição do extrato contém X, a contrapartida é a conta Y."""

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="regras_extrato")
    texto = models.CharField("descrição contém", max_length=100)
    sentido = models.CharField(max_length=7, choices=[("AMBOS", "Entradas e saídas"), ("ENTRADA", "Só entradas"),
                                                      ("SAIDA", "Só saídas")], default="AMBOS")
    conta = models.ForeignKey(Conta, on_delete=models.CASCADE, verbose_name="contrapartida")
    historico = models.CharField("histórico do lançamento", max_length=200, blank=True,
                                 help_text="Vazio = usa a descrição do extrato.")

    class Meta:
        ordering = ["-id"]


class ItemExtrato(models.Model):
    PENDENTE, CONTABILIZADO, IGNORADO = "PENDENTE", "CONTABILIZADO", "IGNORADO"
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="itens_extrato")
    conta_banco = models.ForeignKey(Conta, on_delete=models.PROTECT, related_name="itens_extrato")
    fitid = models.CharField(max_length=80)
    data = models.DateField()
    valor = models.DecimalField(max_digits=15, decimal_places=2, help_text="Positivo = entrada; negativo = saída.")
    descricao = models.CharField(max_length=255)
    contrapartida = models.ForeignKey(Conta, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=13, default=PENDENTE,
                              choices=[(PENDENTE, "Pendente"), (CONTABILIZADO, "Contabilizado"), (IGNORADO, "Ignorado")])
    lancamento = models.ForeignKey(Lancamento, on_delete=models.SET_NULL, null=True, blank=True)
    importado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["data", "id"]
        unique_together = [("conta_banco", "fitid")]
