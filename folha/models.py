"""Folha de pagamento.

As tabelas legais (INSS, IRRF, salário mínimo, salário-família) ficam no banco, com vigência.
Uma tabela só é considerada confiável depois de "conferida" por alguém do escritório contra a
fonte oficial; a folha não fecha com tabela não conferida.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from cobranca.models import Cliente

D = Decimal


class TabelaLegal(models.Model):
    TIPOS = [("INSS", "INSS — empregados"), ("IRRF", "IRRF — tabela mensal"), ("SALARIO_MINIMO", "Salário mínimo"),
             ("SALARIO_FAMILIA", "Salário-família")]
    tipo = models.CharField(max_length=16, choices=TIPOS)
    vigencia_inicio = models.DateField("vigente a partir de")
    dados = models.JSONField(help_text="Faixas e parâmetros da tabela.")
    fonte = models.CharField("fonte (norma)", max_length=200, blank=True)
    conferida = models.BooleanField(default=False)
    conferida_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    conferida_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("tipo", "vigencia_inicio")]
        ordering = ["tipo", "-vigencia_inicio"]
        verbose_name = "tabela legal"
        verbose_name_plural = "tabelas legais"

    def __str__(self):
        return f"{self.get_tipo_display()} desde {self.vigencia_inicio:%m/%Y}"

    @classmethod
    def vigente(cls, tipo, data):
        t = cls.objects.filter(tipo=tipo, vigencia_inicio__lte=data).order_by("-vigencia_inicio").first()
        if not t:
            raise LookupError(f"Não há tabela de {dict(cls.TIPOS)[tipo]} vigente em {data:%m/%Y}. Cadastre em Folha > Tabelas.")
        return t


class ConfigFolha(models.Model):
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, related_name="config_folha")
    cpp = models.DecimalField("INSS patronal (CPP) %", max_digits=5, decimal_places=2, default=D("20"),
                              help_text="Simples (anexos I, II, III e V): 0, pois já está no DAS. Anexo IV: 20.")
    rat = models.DecimalField("RAT/GILRAT %", max_digits=5, decimal_places=2, default=D("2"))
    fap = models.DecimalField("FAP", max_digits=6, decimal_places=4, default=D("1"))
    terceiros = models.DecimalField("terceiros (outras entidades) %", max_digits=5, decimal_places=2, default=D("5.8"),
                                    help_text="Depende do FPAS. Simples: 0.")
    fgts = models.DecimalField("FGTS %", max_digits=5, decimal_places=2, default=D("8"))
    adiantamento_percentual = models.DecimalField("adiantamento quinzenal (%)", max_digits=5, decimal_places=2, default=D("40"))
    divisor_horas = models.PositiveSmallIntegerField("divisor de horas mensais padrão", default=220)
    sindicato = models.CharField("sindicato / convenção", max_length=150, blank=True)
    observacoes = models.TextField(blank=True)

    def aplicar_padrao_regime(self):
        if self.cliente.regime in ("SIMPLES", "MEI"):
            self.cpp, self.rat, self.terceiros = D("0"), D("0"), D("0")
            if self.cliente.regime == "MEI":
                self.cpp = D("3")  # MEI: 3% de CPP sobre o salário do empregado


class Empregado(models.Model):
    TIPOS = [("EMPREGADO", "Empregado (CLT)"), ("APRENDIZ", "Aprendiz"), ("PROLABORE", "Sócio — pró-labore")]
    CONTRATOS = [("INDETERMINADO", "Prazo indeterminado"), ("EXPERIENCIA", "Contrato de experiência"),
                 ("DETERMINADO", "Prazo determinado")]
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="empregados")
    matricula = models.PositiveIntegerField()
    nome = models.CharField(max_length=150)
    cpf = models.CharField("CPF", max_length=14)
    nis = models.CharField("PIS/NIS", max_length=14, blank=True)
    nascimento = models.DateField("data de nascimento", null=True, blank=True)
    email = models.EmailField(blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    endereco = models.CharField("endereço completo", max_length=250, blank=True)
    tipo = models.CharField(max_length=10, choices=TIPOS, default="EMPREGADO")
    categoria_esocial = models.CharField("categoria eSocial", max_length=4, blank=True,
                                         help_text="Código da Tabela 01 do eSocial (ex.: 101 empregado geral, 103 aprendiz).")
    cargo = models.CharField(max_length=100, blank=True)
    cbo = models.CharField("CBO", max_length=7, blank=True)
    admissao = models.DateField("admissão / início")
    contrato = models.CharField(max_length=13, choices=CONTRATOS, default="INDETERMINADO")
    fim_contrato = models.DateField("fim do contrato (experiência/determinado)", null=True, blank=True)
    salario = models.DecimalField("salário / pró-labore mensal (R$)", max_digits=12, decimal_places=2)
    horas_mensais = models.PositiveSmallIntegerField("horas mensais (divisor)", default=220)
    insalubridade = models.PositiveSmallIntegerField("insalubridade (% do salário mínimo)", default=0,
                                                     choices=[(0, "Não"), (10, "Grau mínimo 10%"), (20, "Grau médio 20%"),
                                                              (40, "Grau máximo 40%")])
    periculosidade = models.BooleanField("periculosidade (30% do salário)", default=False)
    vt_custo_mensal = models.DecimalField("custo mensal do vale-transporte (R$)", max_digits=10, decimal_places=2, default=0,
                                          help_text="Desconto de até 6% do salário-base, limitado a este custo. 0 = não usa.")
    pensao_percentual = models.DecimalField("pensão alimentícia (%)", max_digits=5, decimal_places=2, default=0)
    pensao_base = models.CharField("base da pensão", max_length=7, default="LIQUIDO",
                                   choices=[("LIQUIDO", "Sobre o líquido (bruto − INSS − IRRF)"), ("BRUTO", "Sobre o bruto")])
    recebe_adiantamento = models.BooleanField("recebe adiantamento quinzenal", default=False)
    banco = models.CharField("banco / agência / conta ou chave PIX", max_length=120, blank=True)
    demissao = models.DateField("desligamento", null=True, blank=True)
    ativo = models.BooleanField(default=True)
    observacoes = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["cliente", "nome"]
        unique_together = [("cliente", "matricula")]

    def __str__(self):
        return f"{self.matricula} — {self.nome}"

    @property
    def prolabore(self):
        return self.tipo == "PROLABORE"

    def ativo_em(self, inicio, fim):
        return self.admissao <= fim and (not self.demissao or self.demissao >= inicio)


class Dependente(models.Model):
    empregado = models.ForeignKey(Empregado, on_delete=models.CASCADE, related_name="dependentes")
    nome = models.CharField(max_length=150)
    cpf = models.CharField("CPF", max_length=14, blank=True)
    nascimento = models.DateField()
    parentesco = models.CharField(max_length=30, default="Filho(a)")
    ir = models.BooleanField("dependente para IRRF", default=True)
    salario_familia = models.BooleanField("dá direito a salário-família (até 14 anos ou inválido)", default=True)
    invalido = models.BooleanField("inválido (qualquer idade)", default=False)

    def idade_em(self, data):
        return data.year - self.nascimento.year - ((data.month, data.day) < (self.nascimento.month, self.nascimento.day))


class Variavel(models.Model):
    """Lançamentos do mês para um empregado."""

    TIPOS = [
        ("HE50", "Horas extras 50% (horas)"), ("HE100", "Horas extras 100% (horas)"),
        ("NOTURNO", "Adicional noturno 20% (horas)"), ("FALTAS", "Faltas injustificadas (dias)"),
        ("ATRASOS", "Atrasos (horas)"), ("DSR_PERDIDO", "DSR perdido por falta (dias)"),
        ("AFASTAMENTO", "Afastamento pago pelo INSS (dias)"), ("COMISSAO", "Comissões (R$)"),
        ("GRATIFICACAO", "Gratificação (R$)"), ("PREMIO", "Prêmio — sem incidências (R$)"),
        ("OUTRO_PROVENTO", "Outro provento tributável (R$)"), ("DESC_PLANO", "Desconto plano de saúde (R$)"),
        ("DESC_OUTRO", "Outro desconto (R$)"), ("DESC_ADIANT", "Adiantamento pago fora do sistema (R$)"),
    ]
    empregado = models.ForeignKey(Empregado, on_delete=models.CASCADE, related_name="variaveis")
    competencia = models.DateField()
    tipo = models.CharField(max_length=14, choices=TIPOS)
    quantidade = models.DecimalField("quantidade ou valor", max_digits=12, decimal_places=2)
    descricao = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["empregado", "tipo"]


class Folha(models.Model):
    TIPOS = [("MENSAL", "Folha mensal"), ("ADIANTAMENTO", "Adiantamento quinzenal"),
             ("DECIMO1", "13º salário — 1ª parcela"), ("DECIMO2", "13º salário — 2ª parcela")]
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="folhas")
    competencia = models.DateField()
    tipo = models.CharField(max_length=12, choices=TIPOS, default="MENSAL")
    pagamento = models.DateField("data de pagamento", null=True, blank=True)
    fechada = models.BooleanField(default=False)
    fechada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    fechada_em = models.DateTimeField(null=True, blank=True)
    calculada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    calculada_em = models.DateTimeField(null=True)
    resumo = models.JSONField(default=dict)
    avisos = models.JSONField(default=list)

    class Meta:
        unique_together = [("cliente", "competencia", "tipo")]
        ordering = ["-competencia", "tipo"]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.competencia:%m/%Y} — {self.cliente}"


class Holerite(models.Model):
    folha = models.ForeignKey(Folha, on_delete=models.CASCADE, related_name="holerites")
    empregado = models.ForeignKey(Empregado, on_delete=models.PROTECT, related_name="holerites")
    itens = models.JSONField(default=list, help_text="[{codigo, descricao, referencia, provento, desconto}]")
    proventos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    descontos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    liquido = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    base_inss = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    inss = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    base_irrf = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    irrf = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    base_fgts = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fgts = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    memoria = models.JSONField(default=list)

    class Meta:
        unique_together = [("folha", "empregado")]
        ordering = ["empregado__nome"]


class Ferias(models.Model):
    empregado = models.ForeignKey(Empregado, on_delete=models.PROTECT, related_name="ferias")
    aquisitivo_inicio = models.DateField("período aquisitivo — início")
    aquisitivo_fim = models.DateField("período aquisitivo — fim")
    gozo_inicio = models.DateField("início do gozo")
    dias = models.PositiveSmallIntegerField("dias de gozo", default=30)
    abono_dias = models.PositiveSmallIntegerField("dias vendidos (abono pecuniário)", default=0)
    adiantar_13 = models.BooleanField("adiantar 1ª parcela do 13º", default=False)
    medias = models.DecimalField("médias de variáveis (R$/mês)", max_digits=12, decimal_places=2, default=0,
                                 help_text="Média de horas extras, comissões etc. dos últimos 12 meses. Calculada sozinha se ficar 0.")
    valores = models.JSONField(default=dict)
    calculada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    calculada_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-gozo_inicio"]
        verbose_name_plural = "férias"

    @property
    def gozo_fim(self):
        from datetime import timedelta
        return self.gozo_inicio + timedelta(days=self.dias - 1)

    @property
    def pagamento_ate(self):
        from datetime import timedelta
        return self.gozo_inicio - timedelta(days=2)


class Rescisao(models.Model):
    TIPOS = [
        ("SEM_JUSTA_CAUSA", "Dispensa sem justa causa"),
        ("PEDIDO", "Pedido de demissão"),
        ("JUSTA_CAUSA", "Dispensa por justa causa"),
        ("ACORDO", "Acordo entre as partes (art. 484-A da CLT)"),
        ("FIM_EXPERIENCIA", "Término do contrato de experiência/determinado"),
        ("ANTECIPADA_EMPREGADOR", "Rescisão antecipada do contrato a prazo pelo empregador (art. 479)"),
        ("ANTECIPADA_EMPREGADO", "Rescisão antecipada do contrato a prazo pelo empregado (art. 480)"),
    ]
    AVISOS = [("INDENIZADO", "Indenizado"), ("TRABALHADO", "Trabalhado"), ("DISPENSADO", "Dispensado / não se aplica"),
              ("DESCONTADO", "Não cumprido pelo empregado (descontar)")]
    empregado = models.OneToOneField(Empregado, on_delete=models.PROTECT, related_name="rescisao")
    tipo = models.CharField(max_length=21, choices=TIPOS)
    data_aviso = models.DateField("data do aviso / comunicação")
    data_desligamento = models.DateField("último dia trabalhado")
    aviso = models.CharField("aviso prévio", max_length=10, choices=AVISOS, default="INDENIZADO")
    saldo_fgts = models.DecimalField("saldo do FGTS para fins rescisórios (R$)", max_digits=12, decimal_places=2, default=0,
                                     help_text="Do extrato do FGTS Digital, sem o depósito do mês da rescisão.")
    ferias_vencidas_dias = models.PositiveSmallIntegerField("dias de férias vencidas", null=True, blank=True,
                                                            help_text="Vazio = o sistema calcula pelos períodos e férias já gozadas.")
    medias = models.DecimalField("médias de variáveis (R$/mês)", max_digits=12, decimal_places=2, default=0)
    outros_descontos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valores = models.JSONField(default=dict)
    calculada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    calculada_em = models.DateTimeField(auto_now=True)

    @property
    def prazo_pagamento(self):
        from datetime import timedelta
        return self.data_desligamento + timedelta(days=10)  # art. 477, §6º da CLT


def agora():
    return timezone.now()
