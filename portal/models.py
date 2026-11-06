import os
import uuid

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from cobranca.models import Cliente

CATEGORIAS = [
    ("GUIA", "Guias e impostos"),
    ("FOLHA", "Folha / holerites / férias"),
    ("CONTABIL", "Balancete, balanço e DRE"),
    ("FISCAL", "Livros e apurações fiscais"),
    ("SOCIETARIO", "Contratos e societário"),
    ("CERTIDAO", "Certidões"),
    ("EXTRATO", "Extratos bancários"),
    ("NOTAS", "Notas fiscais / XML"),
    ("PESSOAL", "Documentos de funcionários"),
    ("OUTROS", "Outros"),
]

EXTENSOES_PERMITIDAS = {
    ".pdf", ".xml", ".zip", ".jpg", ".jpeg", ".png", ".xlsx", ".xls", ".csv", ".ods",
    ".doc", ".docx", ".odt", ".txt", ".ofx", ".ret", ".rem",
}
TAMANHO_MAXIMO = 25 * 1024 * 1024  # 25 MB por arquivo


def _caminho(instancia, nome):
    ext = os.path.splitext(nome)[1].lower()
    hoje = timezone.localdate()
    return f"clientes/{instancia.cliente_id}/{hoje:%Y/%m}/{uuid.uuid4().hex}{ext}"


class AcessoPortal(models.Model):
    """Login de um cliente no portal. Um login pode ver mais de uma empresa (ex.: sócio de duas)."""

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="acesso_portal")
    clientes = models.ManyToManyField(Cliente, related_name="acessos_portal")
    nome = models.CharField("nome da pessoa", max_length=120)
    telefone = models.CharField("WhatsApp", max_length=20, blank=True)
    AVISOS = [("IMEDIATO", "A cada documento novo"), ("RESUMO", "Um resumo por dia"), ("NENHUM", "Não quero avisos")]
    avisos = models.CharField("avisos por e-mail", max_length=8, choices=AVISOS, default="IMEDIATO")
    ativo = models.BooleanField(default=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "acesso ao portal"
        verbose_name_plural = "acessos ao portal"

    def __str__(self):
        return f"{self.nome} ({self.usuario.username})"


class Protocolo(models.Model):
    ENTREGA, RECEBIMENTO = "ENTREGA", "RECEBIMENTO"
    TIPOS = [(ENTREGA, "Entrega ao cliente"), (RECEBIMENTO, "Recebimento do cliente")]
    MEIOS = [("PORTAL", "Portal do cliente"), ("EMAIL", "E-mail"), ("WHATSAPP", "WhatsApp"),
             ("PESSOAL", "Pessoalmente"), ("CORREIO", "Correio / motoboy")]
    FORMAS_CONFIRMACAO = [("DOWNLOAD", "Download no portal"), ("CLIQUE", "Confirmação no portal"),
                          ("ASSINATURA", "Assinatura no papel"), ("ENVIO", "Enviado pelo próprio cliente"),
                          ("ESCRITORIO", "Registrado pelo escritório")]

    ano = models.PositiveSmallIntegerField(editable=False)
    numero = models.PositiveIntegerField(editable=False)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="protocolos")
    tipo = models.CharField(max_length=11, choices=TIPOS, default=ENTREGA)
    meio = models.CharField(max_length=8, choices=MEIOS, default="PESSOAL")
    data = models.DateTimeField(default=timezone.now)
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+", verbose_name="responsável no escritório")
    contato_nome = models.CharField("pessoa do cliente", max_length=120, blank=True,
                                    help_text="Quem recebeu (entrega) ou quem trouxe (recebimento).")
    observacoes = models.TextField("observações", blank=True)

    confirmado_em = models.DateTimeField(null=True, blank=True)
    confirmado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="+")
    confirmado_ip = models.GenericIPAddressField(null=True, blank=True)
    confirmacao_forma = models.CharField(max_length=10, choices=FORMAS_CONFIRMACAO, blank=True)

    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data", "-numero"]
        unique_together = [("ano", "numero")]

    def __str__(self):
        return f"Protocolo {self.codigo}"

    @property
    def codigo(self):
        return f"{self.ano}/{self.numero:05d}"

    @property
    def confirmado(self):
        return self.confirmado_em is not None

    def save(self, *args, **kwargs):
        if self.pk:
            return super().save(*args, **kwargs)
        self.ano = timezone.localtime(self.data).year if timezone.is_aware(self.data) else self.data.year
        for _ in range(5):  # numeração sequencial por ano, à prova de dois cadastros ao mesmo tempo
            ultimo = Protocolo.objects.filter(ano=self.ano).aggregate(m=models.Max("numero"))["m"] or 0
            self.numero = ultimo + 1
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                self.pk = None
                continue
        raise IntegrityError("Não foi possível numerar o protocolo.")

    def confirmar(self, forma, usuario=None, ip=None):
        if self.confirmado:
            return False
        self.confirmado_em = timezone.now()
        self.confirmado_por = usuario
        self.confirmado_ip = ip
        self.confirmacao_forma = forma
        self.save(update_fields=["confirmado_em", "confirmado_por", "confirmado_ip", "confirmacao_forma"])
        return True


class ItemProtocolo(models.Model):
    protocolo = models.ForeignKey(Protocolo, on_delete=models.CASCADE, related_name="itens")
    descricao = models.CharField("descrição", max_length=200)
    competencia = models.CharField("competência", max_length=7, blank=True, help_text="Ex.: 10/2026")
    quantidade = models.PositiveSmallIntegerField(default=1)
    arquivo = models.ForeignKey("Arquivo", on_delete=models.SET_NULL, null=True, blank=True, related_name="itens_protocolo")

    class Meta:
        ordering = ["id"]


class Arquivo(models.Model):
    ENVIADO, RECEBIDO = "ENVIADO", "RECEBIDO"
    DIRECOES = [(ENVIADO, "Escritório → cliente"), (RECEBIDO, "Cliente → escritório")]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="arquivos")
    direcao = models.CharField(max_length=8, choices=DIRECOES)
    categoria = models.CharField(max_length=10, choices=CATEGORIAS, default="OUTROS")
    titulo = models.CharField("título", max_length=200)
    competencia = models.DateField("competência", null=True, blank=True)
    vencimento = models.DateField(null=True, blank=True)
    valor = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    arquivo = models.FileField(upload_to=_caminho, max_length=200)
    nome_original = models.CharField(max_length=200)
    tamanho = models.PositiveIntegerField(default=0)
    observacao = models.TextField("observação", blank=True)

    enviado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    enviado_em = models.DateTimeField(auto_now_add=True)
    visualizado_em = models.DateTimeField(null=True, blank=True)
    visualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="+")
    processado_em = models.DateTimeField(null=True, blank=True)
    processado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="+")
    protocolo = models.ForeignKey(Protocolo, on_delete=models.SET_NULL, null=True, blank=True, related_name="arquivos")

    class Meta:
        ordering = ["-enviado_em"]

    def __str__(self):
        return self.titulo

    @property
    def extensao(self):
        return os.path.splitext(self.nome_original)[1].lower().lstrip(".")

    @property
    def tamanho_legivel(self):
        t = self.tamanho
        return f"{t / 1048576:.1f} MB" if t >= 1048576 else f"{max(t // 1024, 1)} KB"

    @property
    def vencido(self):
        return self.vencimento and self.vencimento < timezone.localdate() and not self.visualizado_em


class LogPortal(models.Model):
    """Trilha de auditoria do portal (login, download, envio, confirmação)."""

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True)
    acao = models.CharField(max_length=30)
    detalhe = models.CharField(max_length=250, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-em"]


class AvisoEmail(models.Model):
    """Registro de cada e-mail automático (para conferir, evitar repetição e reenviar)."""

    TIPOS = [("DOCUMENTOS", "Documentos publicados"), ("RESUMO", "Resumo diário"), ("GUIA", "Lembrete de guia"),
             ("RECEBIDOS", "Cliente enviou documentos"), ("TESTE", "E-mail de teste")]
    STATUS = [("ENVIADO", "Enviado"), ("ERRO", "Erro"), ("SEM_DESTINO", "Sem e-mail cadastrado"),
              ("SIMULADO", "Não enviado (e-mail ainda não configurado)")]

    tipo = models.CharField(max_length=10, choices=TIPOS)
    chave = models.CharField(max_length=80, unique=True, help_text="Evita mandar o mesmo aviso duas vezes.")
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True)
    destinatarios = models.TextField(blank=True)
    assunto = models.CharField(max_length=200)
    corpo = models.TextField(blank=True)
    corpo_html = models.TextField(blank=True)
    status = models.CharField(max_length=11, choices=STATUS, default="ENVIADO")
    erro = models.TextField(blank=True)
    tentativas = models.PositiveSmallIntegerField(default=0)
    criado_em = models.DateTimeField(auto_now_add=True)
    enviado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "aviso por e-mail"
        verbose_name_plural = "avisos por e-mail"
