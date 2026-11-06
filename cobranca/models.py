from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .validators import so_digitos, validar_documento

DOIS = Decimal("0.01")


def arred(valor):
    return Decimal(valor).quantize(DOIS, rounding=ROUND_HALF_UP)


class Configuracao(models.Model):
    """Configuração única do escritório (sempre registro id=1)."""

    nome_escritorio = models.CharField("nome do escritório", max_length=120, default="Meu Escritório Contábil")
    cnpj = models.CharField("CNPJ", max_length=18, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    cidade = models.CharField(max_length=60, default="SAO PAULO")

    chave_pix = models.CharField(
        "chave PIX", max_length=77, blank=True,
        help_text="Usada para gerar PIX copia-e-cola sem depender de banco/gateway.",
    )
    nome_recebedor_pix = models.CharField("nome do recebedor (PIX)", max_length=25, blank=True)

    multa_percentual = models.DecimalField("multa por atraso (%)", max_digits=5, decimal_places=2, default=Decimal("2.00"))
    juros_mensal_percentual = models.DecimalField("juros ao mês (%)", max_digits=5, decimal_places=2, default=Decimal("1.00"))

    mensagem_cobranca = models.TextField(
        "mensagem de cobrança (WhatsApp/e-mail)",
        default=(
            "Olá, {cliente}! Tudo bem?\n"
            "Segue o honorário contábil referente a {competencia}, no valor de {valor}, "
            "com vencimento em {vencimento}.\n{link}\n"
            "Qualquer dúvida, estamos à disposição.\n{escritorio}"
        ),
        help_text="Variáveis: {cliente} {competencia} {valor} {vencimento} {link} {escritorio} {dias_atraso} {valor_atualizado}",
    )
    mensagem_atraso = models.TextField(
        "mensagem de atraso",
        default=(
            "Olá, {cliente}! Identificamos que o honorário de {competencia} ({valor}), "
            "vencido em {vencimento}, ainda está em aberto ({dias_atraso} dias). "
            "Valor atualizado: {valor_atualizado}.\n{link}\n"
            "Se já pagou, por favor desconsidere e nos envie o comprovante.\n{escritorio}"
        ),
    )
    vencimento_mes_seguinte = models.BooleanField(
        "vencimento no mês seguinte à competência", default=True,
        help_text="Marcado: honorário de outubro vence em novembro. Desmarcado: vence no próprio mês.",
    )
    lembrete_dias_antes =models.PositiveSmallIntegerField("lembrete X dias antes do vencimento", default=3)
    lembrete_dias_depois = models.CharField(
        "lembretes após vencimento (dias)", max_length=40, default="1,7,15",
        help_text="Lista separada por vírgula. Ex.: 1,7,15",
    )
    lembretes_email_ativos = models.BooleanField("enviar lembretes automáticos por e-mail", default=False)

    class Meta:
        verbose_name = "configuração"
        verbose_name_plural = "configuração"

    def __str__(self):
        return self.nome_escritorio

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def atual(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def dias_lembrete_apos(self):
        dias = []
        for parte in self.lembrete_dias_depois.split(","):
            parte = parte.strip()
            if parte.isdigit():
                dias.append(int(parte))
        return dias


class Cliente(models.Model):
    PF, PJ = "PF", "PJ"
    REGIMES = [
        ("MEI", "MEI"),
        ("SIMPLES", "Simples Nacional"),
        ("PRESUMIDO", "Lucro Presumido"),
        ("REAL", "Lucro Real"),
        ("PF", "Pessoa Física / IRPF"),
        ("IMUNE", "Imune / Isenta / Terceiro setor"),
        ("OUTRO", "Outro"),
    ]

    nome = models.CharField("razão social / nome", max_length=150)
    nome_fantasia = models.CharField(max_length=150, blank=True)
    documento = models.CharField("CNPJ/CPF", max_length=18, unique=True)
    regime = models.CharField("regime tributário", max_length=10, choices=REGIMES, default="SIMPLES")
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="clientes", verbose_name="responsável no escritório",
    )

    tem_funcionarios = models.BooleanField(
        "tem funcionários / pró-labore", default=False,
        help_text="Define se entram as obrigações de folha (eSocial, FGTS Digital...).",
    )
    inscricao_estadual = models.CharField("inscrição estadual", max_length=20, blank=True)
    inscricao_municipal = models.CharField("inscrição municipal", max_length=20, blank=True)

    contato = models.CharField("pessoa de contato", max_length=100, blank=True)
    email = models.EmailField("e-mail para cobrança", blank=True)
    whatsapp = models.CharField("WhatsApp", max_length=20, blank=True, help_text="Com DDD. Ex.: 11999998888")

    cep = models.CharField(max_length=9, blank=True)
    endereco = models.CharField("endereço", max_length=150, blank=True)
    numero = models.CharField(max_length=20, blank=True)
    bairro = models.CharField(max_length=80, blank=True)
    cidade = models.CharField(max_length=80, blank=True)
    uf = models.CharField("UF", max_length=2, blank=True)

    observacoes = models.TextField("observações", blank=True)
    ativo = models.BooleanField(default=True)
    asaas_id = models.CharField(max_length=40, blank=True, editable=False)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nome"]

    def __str__(self):
        return self.nome_fantasia or self.nome

    def clean(self):
        validar_documento(self.documento)

    def save(self, *args, **kwargs):
        self.documento = so_digitos(self.documento)
        self.uf = self.uf.upper()
        super().save(*args, **kwargs)

    @property
    def responsavel_nome(self):
        if not self.responsavel:
            return "—"
        return self.responsavel.get_full_name() or self.responsavel.username

    @property
    def tipo_pessoa(self):
        return self.PF if len(self.documento) == 11 else self.PJ

    @property
    def documento_formatado(self):
        d = self.documento
        if len(d) == 14:
            return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
        if len(d) == 11:
            return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
        return d

    @property
    def whatsapp_internacional(self):
        d = so_digitos(self.whatsapp)
        if not d:
            return ""
        return d if d.startswith("55") and len(d) > 11 else "55" + d

    def valor_mensal_total(self):
        return sum((c.valor_mensal for c in self.contratos.filter(ativo=True)), Decimal("0"))

    def cobrancas_em_atraso(self):
        return self.cobrancas.filter(status=Cobranca.ABERTA, vencimento__lt=timezone.localdate())


class Contrato(models.Model):
    """Honorário recorrente. Um cliente pode ter vários (ex.: contábil + folha)."""

    INDICES = [
        ("IPCA", "IPCA"),
        ("IGPM", "IGP-M"),
        ("INPC", "INPC"),
        ("SM", "Salário mínimo"),
        ("FIXO", "Percentual fixo"),
        ("NENHUM", "Sem reajuste automático"),
    ]
    FORMAS = [
        ("UNDEFINED", "Boleto + PIX (cliente escolhe)"),
        ("BOLETO", "Boleto"),
        ("PIX", "PIX"),
        ("MANUAL", "Manual (depósito/dinheiro/PIX próprio)"),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="contratos")
    descricao = models.CharField("serviço", max_length=120, default="Honorários contábeis mensais")
    valor_mensal = models.DecimalField("valor mensal (R$)", max_digits=12, decimal_places=2)
    dia_vencimento = models.PositiveSmallIntegerField("dia do vencimento", default=10)
    data_inicio = models.DateField("início", default=date.today)
    data_fim = models.DateField("encerramento", null=True, blank=True)

    indice_reajuste = models.CharField("índice de reajuste", max_length=6, choices=INDICES, default="IPCA")
    percentual_fixo = models.DecimalField(
        "percentual fixo (%)", max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Só quando o índice for 'Percentual fixo'.",
    )
    mes_reajuste = models.PositiveSmallIntegerField(
        "mês do reajuste", default=1, choices=[(i, f"{i:02d}") for i in range(1, 13)],
    )
    ultimo_reajuste = models.DateField("último reajuste", null=True, blank=True)

    cobra_13 = models.BooleanField("cobra 13º honorário", default=True)
    mes_13 = models.PositiveSmallIntegerField(
        "mês do 13º", default=12, choices=[(i, f"{i:02d}") for i in range(1, 13)],
    )
    forma_pagamento = models.CharField("forma de pagamento", max_length=10, choices=FORMAS, default="UNDEFINED")
    ativo = models.BooleanField(default=True)

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["cliente__nome", "descricao"]

    def __str__(self):
        return f"{self.cliente} — {self.descricao}"

    def clean(self):
        if not 1 <= (self.dia_vencimento or 0) <= 31:
            raise ValidationError({"dia_vencimento": "Informe um dia entre 1 e 31."})
        if self.indice_reajuste == "FIXO" and self.percentual_fixo is None:
            raise ValidationError({"percentual_fixo": "Informe o percentual fixo."})

    def vigente_em(self, competencia):
        """competencia = date no dia 1 do mês."""
        if not self.ativo:
            return False
        inicio = self.data_inicio.replace(day=1)
        if competencia < inicio:
            return False
        if self.data_fim and competencia > self.data_fim.replace(day=1):
            return False
        return True

    def reajuste_pendente(self, hoje=None):
        hoje = hoje or timezone.localdate()
        if not self.ativo or self.indice_reajuste == "NENHUM":
            return False
        if hoje.month != self.mes_reajuste:
            return False
        # ainda não completou um ano de contrato
        if (hoje.year - self.data_inicio.year) * 12 + hoje.month - self.data_inicio.month < 12:
            return False
        if self.ultimo_reajuste and (hoje - self.ultimo_reajuste).days < 300:
            return False
        return True


class HistoricoReajuste(models.Model):
    contrato = models.ForeignKey(Contrato, on_delete=models.CASCADE, related_name="reajustes")
    data = models.DateField(default=date.today)
    indice = models.CharField(max_length=6)
    periodo = models.CharField("período do índice", max_length=40, blank=True)
    percentual = models.DecimalField(max_digits=7, decimal_places=4)
    valor_anterior = models.DecimalField(max_digits=12, decimal_places=2)
    valor_novo = models.DecimalField(max_digits=12, decimal_places=2)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-data", "-id"]
        verbose_name = "histórico de reajuste"
        verbose_name_plural = "histórico de reajustes"


class Cobranca(models.Model):
    ABERTA, PAGA, CANCELADA = "ABERTA", "PAGA", "CANCELADA"
    STATUS = [(ABERTA, "Em aberto"), (PAGA, "Paga"), (CANCELADA, "Cancelada")]
    TIPOS = [("MENSAL", "Mensalidade"), ("13", "13º honorário"), ("AVULSO", "Serviço avulso")]
    FORMAS_RECEBIMENTO = [
        ("BOLETO", "Boleto"), ("PIX", "PIX"), ("TED", "TED/Depósito"),
        ("DINHEIRO", "Dinheiro"), ("CARTAO", "Cartão"), ("OUTRO", "Outro"),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="cobrancas")
    contrato = models.ForeignKey(Contrato, on_delete=models.SET_NULL, null=True, blank=True, related_name="cobrancas")
    tipo = models.CharField(max_length=6, choices=TIPOS, default="MENSAL")
    competencia = models.DateField("competência", help_text="Sempre dia 1 do mês de referência.")
    descricao = models.CharField("descrição", max_length=200)
    valor = models.DecimalField("valor (R$)", max_digits=12, decimal_places=2)
    vencimento = models.DateField()

    status = models.CharField(max_length=10, choices=STATUS, default=ABERTA)
    data_pagamento = models.DateField("data do pagamento", null=True, blank=True)
    valor_pago = models.DecimalField("valor pago (R$)", max_digits=12, decimal_places=2, null=True, blank=True)
    forma_recebimento = models.CharField(max_length=10, choices=FORMAS_RECEBIMENTO, blank=True)

    gateway_id = models.CharField(max_length=40, blank=True, db_index=True)
    link_fatura = models.URLField(max_length=300, blank=True)
    link_boleto = models.URLField(max_length=300, blank=True)
    pix_copia_cola = models.TextField(blank=True)

    observacoes = models.TextField("observações", blank=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    baixado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-vencimento", "cliente__nome"]
        verbose_name = "cobrança"
        constraints = [
            models.UniqueConstraint(
                fields=["contrato", "competencia", "tipo"],
                condition=models.Q(tipo__in=["MENSAL", "13"]) & ~models.Q(status="CANCELADA"),
                name="uma_cobranca_por_contrato_competencia",
            )
        ]

    def __str__(self):
        return f"{self.cliente} — {self.competencia:%m/%Y} — R$ {self.valor}"

    # ---- situação ----
    @property
    def vencida(self):
        return self.status == self.ABERTA and self.vencimento < timezone.localdate()

    @property
    def situacao(self):
        if self.status == self.ABERTA and self.vencida:
            return "VENCIDA"
        return self.status

    @property
    def situacao_display(self):
        return {"VENCIDA": "Vencida"}.get(self.situacao) or self.get_status_display()

    def dias_atraso(self, hoje=None):
        hoje = hoje or timezone.localdate()
        if self.status != self.ABERTA:
            return 0
        return max((hoje - self.vencimento).days, 0)

    def encargos(self, hoje=None, config=None):
        """Multa + juros simples pro rata dia (juros mensal / 30)."""
        dias = self.dias_atraso(hoje)
        if not dias:
            return Decimal("0.00")
        config = config or Configuracao.atual()
        multa = self.valor * config.multa_percentual / 100
        juros = self.valor * config.juros_mensal_percentual / 100 / 30 * dias
        return arred(multa + juros)

    def valor_atualizado(self, hoje=None, config=None):
        return arred(self.valor + self.encargos(hoje, config))

    @property
    def link_pagamento(self):
        return self.link_fatura or self.link_boleto

    def registrar_pagamento(self, data_pagamento, valor_pago=None, forma="", usuario=None):
        self.status = self.PAGA
        self.data_pagamento = data_pagamento
        self.valor_pago = valor_pago if valor_pago is not None else self.valor
        self.forma_recebimento = forma
        self.baixado_por = usuario
        self.save()


class LembreteEnviado(models.Model):
    """Evita mandar o mesmo lembrete duas vezes."""

    cobranca = models.ForeignKey(Cobranca, on_delete=models.CASCADE, related_name="lembretes")
    chave = models.CharField(max_length=20)  # ex.: "antes-3", "apos-7"
    canal = models.CharField(max_length=10, default="email")
    enviado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("cobranca", "chave", "canal")]
