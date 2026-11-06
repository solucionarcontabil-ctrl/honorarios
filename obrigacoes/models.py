import calendar
from datetime import date

from django.conf import settings
from django.db import models
from django.utils import timezone

from cobranca.models import Cliente

from .calendario import Calendario


class Feriado(models.Model):
    """Feriados estaduais/municipais (os nacionais já são calculados automaticamente)."""

    data = models.DateField(unique=True)
    descricao = models.CharField("descrição", max_length=80)

    class Meta:
        ordering = ["data"]

    def __str__(self):
        return f"{self.data:%d/%m/%Y} — {self.descricao}"


class Obrigacao(models.Model):
    ORGAOS = [("RFB", "Receita Federal"), ("SEFAZ", "SEFAZ (estado)"), ("PREFEITURA", "Prefeitura"),
              ("CAIXA", "Caixa / FGTS"), ("MTE", "Trabalho / eSocial"), ("INTERNO", "Rotina interna"), ("OUTRO", "Outro")]
    PERIODICIDADES = [("MENSAL", "Mensal"), ("TRIMESTRAL", "Trimestral"), ("ANUAL", "Anual")]
    REGRAS = [("DIA_FIXO", "Dia fixo do mês"), ("DIA_UTIL", "N-ésimo dia útil"), ("ULTIMO_DIA_UTIL", "Último dia útil")]
    AJUSTES = [("POSTERGA", "Prorroga para o próximo dia útil"), ("ANTECIPA", "Antecipa para o dia útil anterior"),
               ("MANTEM", "Mantém a data")]

    sigla = models.CharField(max_length=30, unique=True)
    nome = models.CharField(max_length=120)
    orgao = models.CharField("órgão", max_length=10, choices=ORGAOS, default="RFB")
    periodicidade = models.CharField(max_length=10, choices=PERIODICIDADES, default="MENSAL")
    regra_prazo = models.CharField("regra do prazo", max_length=16, choices=REGRAS, default="DIA_FIXO")
    dia = models.PositiveSmallIntegerField("dia", default=20, help_text="Dia fixo, ou qual dia útil (ex.: 10 = 10º dia útil).")
    meses_apos = models.PositiveSmallIntegerField(
        "vence quantos meses após a competência", default=1,
        help_text="Mensal/trimestral: 1 = mês seguinte; 2 = segundo mês seguinte. Anual: ignorado.",
    )
    mes_anual = models.PositiveSmallIntegerField(
        "mês de entrega (anuais)", null=True, blank=True, choices=[(i, f"{i:02d}") for i in range(1, 13)],
    )
    ajuste = models.CharField("se cair em dia não útil", max_length=8, choices=AJUSTES, default="POSTERGA")
    regimes = models.CharField(
        "regimes que se aplicam (padrão)", max_length=80, blank=True,
        help_text="Códigos separados por vírgula: MEI, SIMPLES, PRESUMIDO, REAL, PF, IMUNE, OUTRO.",
    )
    so_com_funcionarios = models.BooleanField("só para quem tem funcionários/pró-labore", default=False)
    ativa = models.BooleanField(default=True)
    observacao = models.TextField("observação", blank=True)

    class Meta:
        ordering = ["periodicidade", "sigla"]
        verbose_name = "obrigação"
        verbose_name_plural = "obrigações"

    def __str__(self):
        return self.sigla

    def lista_regimes(self):
        return [r.strip().upper() for r in self.regimes.split(",") if r.strip()]

    def aplica_ao_cliente(self, cliente):
        if cliente.regime not in self.lista_regimes():
            return False
        if self.so_com_funcionarios and not cliente.tem_funcionarios:
            return False
        return True

    # --- cálculo -------------------------------------------------------
    def competencia_para_vencimento(self, ano, mes):
        """Dado o mês de vencimento, devolve a competência (date dia 1) ou None se não vence nesse mês."""
        if self.periodicidade == "ANUAL":
            if self.mes_anual != mes:
                return None
            return date(ano - 1, 1, 1)  # ano-base anterior
        total = ano * 12 + (mes - 1) - self.meses_apos
        comp = date(total // 12, total % 12 + 1, 1)
        if self.periodicidade == "TRIMESTRAL" and comp.month not in (3, 6, 9, 12):
            return None
        return comp

    def prazo_no_mes(self, ano, mes, cal=None):
        cal = cal or Calendario()
        if self.regra_prazo == "ULTIMO_DIA_UTIL":
            return cal.ultimo_dia_util(ano, mes)
        if self.regra_prazo == "DIA_UTIL":
            return cal.enesimo_dia_util(ano, mes, self.dia)
        d = date(ano, mes, min(self.dia, calendar.monthrange(ano, mes)[1]))
        return cal.ajustar(d, self.ajuste)

    def descricao_prazo(self):
        if self.regra_prazo == "ULTIMO_DIA_UTIL":
            base = "último dia útil"
        elif self.regra_prazo == "DIA_UTIL":
            base = f"{self.dia}º dia útil"
        else:
            base = f"dia {self.dia}"
        if self.periodicidade == "ANUAL":
            return f"{base} de {self.mes_anual:02d}" if self.mes_anual else base
        quando = {0: "do próprio mês", 1: "do mês seguinte", 2: "do 2º mês seguinte"}.get(self.meses_apos, f"+{self.meses_apos} meses")
        return f"{base} {quando}"


class ClienteObrigacao(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="obrigacoes")
    obrigacao = models.ForeignKey(Obrigacao, on_delete=models.CASCADE, related_name="clientes")
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Vazio = responsável do cliente.",
    )

    class Meta:
        unique_together = [("cliente", "obrigacao")]
        ordering = ["obrigacao__sigla"]

    def __str__(self):
        return f"{self.cliente} — {self.obrigacao}"


class Entrega(models.Model):
    """Uma obrigação de um cliente numa competência — ou uma tarefa avulsa."""

    PENDENTE, ANDAMENTO, ENTREGUE, DISPENSADA = "PENDENTE", "ANDAMENTO", "ENTREGUE", "DISPENSADA"
    STATUS = [(PENDENTE, "Pendente"), (ANDAMENTO, "Em andamento"), (ENTREGUE, "Entregue / feita"),
              (DISPENSADA, "Dispensada / sem movimento")]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="entregas", null=True, blank=True)
    obrigacao = models.ForeignKey(Obrigacao, on_delete=models.SET_NULL, related_name="entregas", null=True, blank=True)
    titulo = models.CharField("título", max_length=150)
    competencia = models.DateField("competência", null=True, blank=True)
    prazo = models.DateField()
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="entregas",
    )
    status = models.CharField(max_length=10, choices=STATUS, default=PENDENTE)
    concluido_em = models.DateTimeField("concluído em", null=True, blank=True)
    concluido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    protocolo = models.CharField("protocolo / recibo", max_length=120, blank=True)
    observacoes = models.TextField("observações", blank=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["prazo", "cliente__nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["cliente", "obrigacao", "competencia"],
                condition=models.Q(obrigacao__isnull=False),
                name="uma_entrega_por_obrigacao_competencia",
            )
        ]

    def __str__(self):
        return self.titulo

    @property
    def aberta(self):
        return self.status in (self.PENDENTE, self.ANDAMENTO)

    @property
    def atrasada(self):
        return self.aberta and self.prazo < timezone.localdate()

    @property
    def situacao(self):
        if self.atrasada:
            return "ATRASADA"
        if self.aberta and self.prazo == timezone.localdate():
            return "HOJE"
        return self.status

    @property
    def situacao_display(self):
        return {"ATRASADA": "Atrasada", "HOJE": "Vence hoje"}.get(self.situacao) or self.get_status_display()

    @property
    def responsavel_nome(self):
        u = self.responsavel
        return (u.get_full_name() or u.username) if u else "—"

    def concluir(self, usuario, status=ENTREGUE, protocolo=""):
        self.status = status
        self.concluido_em = timezone.now()
        self.concluido_por = usuario
        if protocolo:
            self.protocolo = protocolo
        self.save()

    def reabrir(self):
        self.status = self.PENDENTE
        self.concluido_em = None
        self.concluido_por = None
        self.save()


class Certificado(models.Model):
    TIPOS = [("A1", "A1 (arquivo)"), ("A3", "A3 (token/cartão)"), ("NUVEM", "Em nuvem")]
    MODELOS = [("ECNPJ", "e-CNPJ"), ("ECPF", "e-CPF"), ("NFE", "NF-e"), ("OUTRO", "Outro")]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="certificados", null=True, blank=True,
                                help_text="Vazio = certificado do próprio escritório.")
    tipo = models.CharField(max_length=5, choices=TIPOS, default="A1")
    modelo = models.CharField(max_length=5, choices=MODELOS, default="ECNPJ")
    titular = models.CharField(max_length=150)
    documento = models.CharField("CNPJ/CPF do titular", max_length=18, blank=True)
    emissor = models.CharField("autoridade certificadora", max_length=120, blank=True)
    validade_inicio = models.DateField("válido de", null=True, blank=True)
    validade_fim = models.DateField("válido até")
    onde_esta = models.CharField("onde está guardado", max_length=150, blank=True,
                                 help_text="Ex.: pasta do escritório, token com o cliente, instalado no PC do litoral.")
    procuracao_ecac = models.BooleanField("cliente tem procuração eletrônica no e-CAC para o escritório", default=False)
    observacoes = models.TextField("observações", blank=True)
    ativo = models.BooleanField(default=True, help_text="Desmarque quando for substituído por outro.")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["validade_fim"]

    def __str__(self):
        return f"{self.titular} ({self.get_tipo_display()}) até {self.validade_fim:%d/%m/%Y}"

    def dias_para_vencer(self):
        return (self.validade_fim - timezone.localdate()).days

    @property
    def situacao(self):
        d = self.dias_para_vencer()
        if d < 0:
            return "VENCIDO"
        if d <= 30:
            return "VENCENDO"
        return "OK"
