import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from cobranca.models import Cliente, Configuracao

from . import avisos, services
from .models import AvisoEmail

MEDIA = tempfile.mkdtemp()


def pdf(nome="guia.pdf"):
    return SimpleUploadedFile(nome, b"%PDF-1.4", content_type="application/pdf")


@override_settings(MEDIA_ROOT=MEDIA, SITE_URL="https://honorarios.exemplo.com.br",
                   DEFAULT_FROM_EMAIL="avisos@exemplo.com.br")
class AvisosTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        U = get_user_model()
        self.contador = U.objects.create_superuser("contador", "contador@escritorio.com", "senha-muito-forte-1")
        self.cli = Cliente.objects.create(nome="Padaria", documento="11222333000181", responsavel=self.contador)
        self.maria, _ = services.criar_acesso("Maria", "maria", [self.cli], self.contador, "maria@padaria.com")
        self.joao, _ = services.criar_acesso("João", "joao", [self.cli], self.contador, "joao@padaria.com", avisos="RESUMO")
        self.ze, _ = services.criar_acesso("Zé", "ze", [self.cli], self.contador, "ze@padaria.com", avisos="NENHUM")
        cfg = Configuracao.atual()
        cfg.nome_escritorio, cfg.email = "Contabilidade Teste", "contato@escritorio.com"
        cfg.save()

    def publicar(self, arquivos, **kw):
        with self.captureOnCommitCallbacks(execute=True):
            return services.publicar(self.cli, arquivos, self.contador, kw.pop("categoria", "GUIA"), **kw)

    def test_um_email_por_publicacao_so_para_imediato(self):
        self.publicar([pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf")], titulo="Folha 10/2026")
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ["maria@padaria.com"])
        self.assertEqual(m.from_email, "Contabilidade Teste <avisos@exemplo.com.br>")
        self.assertEqual(m.reply_to, ["contato@escritorio.com"])
        self.assertIn("3 novos documentos", m.subject)
        self.assertIn("https://honorarios.exemplo.com.br/portal/documentos/", m.body)
        self.assertIn("<b>Folha 10/2026 — a</b>", m.alternatives[0][0])
        self.assertEqual(AvisoEmail.objects.get().status, "ENVIADO")

    def test_desligado_nas_configuracoes(self):
        cfg = Configuracao.atual()
        cfg.avisos_portal_ativos = False
        cfg.save()
        self.publicar([pdf()])
        self.assertEqual(len(mail.outbox), 0)

    def test_falha_de_email_nao_impede_publicacao_e_reenvia(self):
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("SMTP fora do ar")):
            prot, criados = self.publicar([pdf()])
        self.assertEqual(len(criados), 1)
        aviso = AvisoEmail.objects.get()
        self.assertEqual((aviso.status, aviso.erro), ("ERRO", "SMTP fora do ar"))
        self.assertEqual(avisos.reenviar_com_erro(), 1)
        aviso.refresh_from_db()
        self.assertEqual(aviso.status, "ENVIADO")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_sem_conta_de_envio_so_registra(self):
        self.publicar([pdf()])
        self.assertEqual(AvisoEmail.objects.get().status, "SIMULADO")

    def test_equipe_avisada_quando_cliente_envia(self):
        with self.captureOnCommitCallbacks(execute=True):
            services.receber(self.cli, [pdf("extrato.pdf")], self.maria.usuario, "EXTRATO", observacao="conta Itaú")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["contador@escritorio.com"])
        self.assertIn("Padaria enviou 1 documento", mail.outbox[0].subject)
        self.assertIn("conta Itaú", mail.outbox[0].body)

    def test_resumo_diario_sem_repetir(self):
        self.publicar([pdf("x.pdf")], categoria="FOLHA")
        mail.outbox.clear()
        self.assertEqual(avisos.resumo_diario(), 1)
        self.assertEqual(mail.outbox[0].to, ["joao@padaria.com"])
        self.assertEqual(avisos.resumo_diario(), 0)  # mesmo dia: não repete

    def test_lembrete_guia_nao_baixada(self):
        amanha = timezone.localdate() + timedelta(days=1)
        _, [g] = self.publicar([pdf()], titulo="DAS 10/2026", vencimento=amanha, valor=Decimal("312.40"))
        mail.outbox.clear()
        self.assertEqual(avisos.lembrar_guias(), 1)
        m = mail.outbox[0]
        self.assertEqual(sorted(m.to), ["joao@padaria.com", "maria@padaria.com"])  # NENHUM fica de fora
        self.assertIn("R$ 312,40", m.body)
        self.assertEqual(avisos.lembrar_guias(), 0)
        # se baixou, não lembra
        g.visualizado_em = timezone.now()
        g.save()
        AvisoEmail.objects.filter(tipo="GUIA").delete()
        self.assertEqual(avisos.lembrar_guias(), 0)

    def test_rotina_diaria_e_telas(self):
        call_command("rotina_diaria", stdout=open("/dev/null", "w"))
        self.client.force_login(self.contador)
        self.publicar([pdf()])
        a = AvisoEmail.objects.first()
        for url in ["/avisos/", "/avisos/?status=ERRO", f"/avisos/{a.pk}/", "/acessos/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        mail.outbox.clear()
        self.client.post("/avisos/teste/", {"para": "eu@escritorio.com"})
        self.assertEqual(mail.outbox[0].to, ["eu@escritorio.com"])
        self.client.post(f"/acessos/{self.ze.pk}/avisos/", {"avisos": "IMEDIATO", "email": "ze2@padaria.com"})
        self.ze.refresh_from_db()
        self.assertEqual((self.ze.avisos, self.ze.usuario.email), ("IMEDIATO", "ze2@padaria.com"))

    def test_cliente_muda_preferencia_no_portal(self):
        self.client.force_login(self.maria.usuario)
        r = self.client.post("/portal/senha/", {"parte": "avisos", "avisos": "NENHUM", "email": "nova@padaria.com"})
        self.assertEqual(r.status_code, 302)
        self.maria.refresh_from_db()
        self.assertEqual((self.maria.avisos, self.maria.usuario.email), ("NENHUM", "nova@padaria.com"))
        self.assertContains(self.client.get("/portal/senha/"), "Avisos por e-mail")
