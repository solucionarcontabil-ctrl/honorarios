import io
from datetime import date, datetime, timedelta, timezone as tz

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from cobranca.importacao import analisar, importar
from cobranca.models import Cliente

from . import services
from .calendario import Calendario, pascoa
from .models import Certificado, ClienteObrigacao, Entrega, Feriado, Obrigacao


class CalendarioTest(TestCase):
    def test_pascoa(self):
        self.assertEqual(pascoa(2025), date(2025, 4, 20))
        self.assertEqual(pascoa(2026), date(2026, 4, 5))

    def test_dias_uteis(self):
        cal = Calendario(extras=[])
        self.assertFalse(cal.dia_util(date(2026, 2, 16)))  # carnaval
        self.assertFalse(cal.dia_util(date(2026, 4, 3)))   # sexta-feira santa
        self.assertFalse(cal.dia_util(date(2026, 11, 20)))  # consciência negra
        self.assertEqual(cal.ultimo_dia_util(2026, 5), date(2026, 5, 29))
        # 5º dia útil de jan/2026: 1º é feriado -> 2,5,6,7,8
        self.assertEqual(cal.enesimo_dia_util(2026, 1, 5), date(2026, 1, 8))

    def test_feriado_municipal(self):
        Feriado.objects.create(data=date(2026, 1, 26), descricao="Aniversário da cidade")
        self.assertFalse(Calendario().dia_util(date(2026, 1, 26)))


class PrazoTest(TestCase):
    def test_das_cai_no_sabado_prorroga(self):
        das = Obrigacao.objects.get(sigla="DAS")  # vem do catálogo padrão
        # 20/06/2026 é sábado -> 22/06 (segunda)
        self.assertEqual(das.prazo_no_mes(2026, 6, Calendario(extras=[])), date(2026, 6, 22))
        self.assertEqual(das.competencia_para_vencimento(2026, 6), date(2026, 5, 1))

    def test_fgts_antecipa(self):
        fgts = Obrigacao.objects.get(sigla="FGTS")
        # 20/09/2026 é domingo -> 18/09 (sexta)
        self.assertEqual(fgts.prazo_no_mes(2026, 9, Calendario(extras=[])), date(2026, 9, 18))

    def test_trimestral_e_anual(self):
        irpj = Obrigacao.objects.get(sigla="IRPJ-CSLL")
        self.assertEqual(irpj.competencia_para_vencimento(2026, 4), date(2026, 3, 1))
        self.assertIsNone(irpj.competencia_para_vencimento(2026, 5))
        ecf = Obrigacao.objects.get(sigla="ECF")
        self.assertEqual(ecf.competencia_para_vencimento(2026, 7), date(2025, 1, 1))
        self.assertIsNone(ecf.competencia_para_vencimento(2026, 6))

    def test_efd_contribuicoes_segundo_mes(self):
        efd = Obrigacao.objects.get(sigla="EFD-CONTRIB")
        self.assertEqual(efd.competencia_para_vencimento(2026, 3), date(2026, 1, 1))


class GeracaoTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "senha-muito-forte-1")
        self.simples = Cliente.objects.create(nome="Padaria", documento="11222333000181", regime="SIMPLES",
                                              tem_funcionarios=True, responsavel=self.user)
        self.mei = Cliente.objects.create(nome="MEI", documento="11444777000161", regime="MEI")

    def test_padrao_regime(self):
        services.aplicar_padrao_regime(self.simples)
        siglas = set(self.simples.obrigacoes.values_list("obrigacao__sigla", flat=True))
        self.assertIn("DAS", siglas)
        self.assertIn("ESOCIAL", siglas)
        self.assertNotIn("EFD-CONTRIB", siglas)
        services.aplicar_padrao_regime(self.mei)
        self.assertEqual(set(self.mei.obrigacoes.values_list("obrigacao__sigla", flat=True)), {"DAS-MEI", "DASN-SIMEI"})

    def test_gerar_sem_duplicar(self):
        services.aplicar_padrao_regime(self.simples)
        criadas = services.gerar_mes(2026, 3, self.user)
        titulos = {e.titulo for e in criadas}
        self.assertIn("DAS 02/2026", titulos)
        self.assertIn("DEFIS 2025", titulos)
        self.assertTrue(all(e.responsavel == self.user for e in criadas))
        self.assertEqual(services.gerar_mes(2026, 3, self.user), [])

    def test_concluir(self):
        ClienteObrigacao.objects.create(cliente=self.mei, obrigacao=Obrigacao.objects.get(sigla="DAS-MEI"))
        e = services.gerar_mes(2026, 3)[0]
        e.concluir(self.user, protocolo="123")
        self.assertFalse(e.aberta)
        e.reabrir()
        self.assertTrue(e.aberta)


def _pfx(nome="EMPRESA TESTE LTDA:11222333000181", dias=200, senha=b"1234"):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
    from cryptography.x509.oid import NameOID

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeito = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nome)])
    emissor = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AC TESTE")])
    agora = datetime.now(tz.utc)
    cert = (x509.CertificateBuilder().subject_name(sujeito).issuer_name(emissor).public_key(chave.public_key())
            .serial_number(1).not_valid_before(agora).not_valid_after(agora + timedelta(days=dias))
            .sign(chave, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b"x", chave, cert, None, BestAvailableEncryption(senha))


class CertificadoTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "senha-muito-forte-1")
        self.client.force_login(self.user)
        self.cli = Cliente.objects.create(nome="Empresa Teste", documento="11222333000181")

    def test_ler_pfx(self):
        d = services.ler_pfx(_pfx(), "1234")
        self.assertEqual(d["documento"], "11222333000181")
        self.assertEqual(d["titular"], "EMPRESA TESTE LTDA")
        with self.assertRaises(services.CertificadoInvalido):
            services.ler_pfx(_pfx(), "errada")

    def test_upload_vincula_e_substitui(self):
        Certificado.objects.create(cliente=self.cli, titular="X", documento="11222333000181",
                                   validade_fim=date.today() + timedelta(days=5))
        arq = SimpleUploadedFile("cert.pfx", _pfx(), content_type="application/x-pkcs12")
        r = self.client.post("/certificados/ler/", {"arquivo": arq, "senha": "1234", "onde_esta": "pasta"})
        self.assertEqual(r.status_code, 302)
        novo = Certificado.objects.get(ativo=True)
        self.assertEqual(novo.cliente, self.cli)
        self.assertEqual(Certificado.objects.filter(ativo=False).count(), 1)


class ImportacaoTest(TestCase):
    CSV = (
        "Relatório de empresas\n"
        "Código;Razão Social;Nome Fantasia;CNPJ/CPF;Tributação;E-mail;Telefone;Município;UF\n"
        "1;PADARIA PAO QUENTE LTDA;Pão Quente;11.222.333/0001-81;Simples Nacional;a@a.com;11 99999-0000;São Paulo;SP\n"
        "2;CLINICA SORRISO;;45723174000110;Lucro Presumido;;;Santos;sp\n"
        "3;CNPJ ERRADO;;11111111111111;Simples;;;;\n"
        "4;JOAO DA SILVA;;529.982.247-25;;;;;\n"
    ).encode("cp1252")

    def test_csv_dominio(self):
        Cliente.objects.create(nome="Clinica", documento="45723174000110", email="")
        linhas, mapa, erro = analisar("empresas.csv", self.CSV)
        self.assertIsNone(erro)
        self.assertEqual([l["situacao"] for l in linhas], ["novo", "existe", "erro", "novo"])
        self.assertEqual(linhas[0]["dados"]["regime"], "SIMPLES")
        criados, atualizados = importar(linhas)
        self.assertEqual(criados, 2)
        self.assertEqual(Cliente.objects.get(documento="11222333000181").nome_fantasia, "Pão Quente")
        self.assertEqual(Cliente.objects.get(documento="52998224725").regime, "PF")
        self.assertEqual(Cliente.objects.get(documento="45723174000110").cidade, "Santos")

    def test_xlsx_com_cnpj_numerico(self):
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.append(["Razão Social", "CNPJ", "Regime"])
        wb.active.append(["EMPRESA ZERO", 1222333000128, "Lucro Real"])  # perdeu zero à esquerda
        buf = io.BytesIO()
        wb.save(buf)
        linhas, _, erro = analisar("x.xlsx", buf.getvalue())
        self.assertIsNone(erro)
        self.assertEqual(linhas[0]["dados"]["documento"], "01222333000128")

    def test_fluxo_tela(self):
        u = get_user_model().objects.create_superuser("admin", "a@a.com", "senha-muito-forte-1")
        self.client.force_login(u)
        arq = SimpleUploadedFile("empresas.csv", self.CSV)
        r = self.client.post("/clientes/importar/", {"arquivo": arq, "responsavel": u.pk, "aplicar_obrigacoes": "on"})
        self.assertContains(r, "Confirmar importação")
        r = self.client.post("/clientes/importar/", {"confirmar": "1"})
        self.assertEqual(r.status_code, 302)
        padaria = Cliente.objects.get(documento="11222333000181")
        self.assertEqual(padaria.responsavel, u)
        self.assertTrue(padaria.obrigacoes.filter(obrigacao__sigla="DAS").exists())


class TelasTest(TestCase):
    def test_telas(self):
        u = get_user_model().objects.create_superuser("admin", "a@a.com", "senha-muito-forte-1")
        self.client.force_login(u)
        c = Cliente.objects.create(nome="Padaria", documento="11222333000181", responsavel=u)
        services.aplicar_padrao_regime(c)
        services.gerar_mes(2026, 3, u)
        e = Entrega.objects.first()
        Certificado.objects.create(cliente=c, titular="X", validade_fim=date.today())
        for url in ["/obrigacoes/", "/obrigacoes/?mes=2026-03", "/obrigacoes/?visao=atrasadas", "/obrigacoes/?visao=semana&responsavel=eu",
                    "/obrigacoes/gerar/?mes=2026-04", "/obrigacoes/catalogo/", "/obrigacoes/catalogo/nova/",
                    f"/obrigacoes/catalogo/{e.obrigacao_id}/", "/obrigacoes/feriados/novo/", f"/obrigacoes/{e.pk}/editar/",
                    "/tarefas/nova/", f"/clientes/{c.pk}/obrigacoes/", "/clientes/importar/", "/certificados/",
                    "/certificados/?situacao=vencendo", "/certificados/novo/", f"/clientes/{c.pk}/", "/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.post(f"/obrigacoes/{e.pk}/entregar/", {"protocolo": "ABC", "proximo": "/obrigacoes/"})
        self.assertEqual(r.status_code, 302)
        e.refresh_from_db()
        self.assertEqual((e.status, e.protocolo, e.concluido_por), (Entrega.ENTREGUE, "ABC", u))
        r = self.client.post("/tarefas/nova/", {"titulo": "Ligar para o cliente", "prazo": "2026-03-10", "responsavel": u.pk})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Entrega.objects.filter(titulo="Ligar para o cliente", obrigacao__isnull=True).exists())
