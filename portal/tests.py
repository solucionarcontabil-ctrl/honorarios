import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from cobranca.models import Cliente, Cobranca

from . import services
from .models import Arquivo, Protocolo

MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA)
class PortalTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.equipe = get_user_model().objects.create_superuser("contador", "c@c.com", "senha-muito-forte-1")
        self.padaria = Cliente.objects.create(nome="Padaria", documento="11222333000181", responsavel=self.equipe,
                                              whatsapp="11999998888")
        self.outra = Cliente.objects.create(nome="Outra", documento="45723174000110")
        self.acesso, self.senha = services.criar_acesso("Maria Silva", "maria@padaria.com", [self.padaria], self.equipe)

    def _arq(self, nome="guia.pdf", conteudo=b"%PDF-1.4 teste"):
        return SimpleUploadedFile(nome, conteudo, content_type="application/pdf")

    def entrar_cliente(self):
        self.assertTrue(self.client.login(username="maria@padaria.com", password=self.senha))

    # ---------------------------------------------------------- segurança
    def test_cliente_nao_ve_sistema_interno(self):
        self.entrar_cliente()
        for url in ["/", "/clientes/", f"/clientes/{self.padaria.pk}/", "/cobrancas/", "/xml/", "/documentos/",
                    "/admin/", "/horas/", "/configuracoes/"]:
            r = self.client.get(url)
            self.assertEqual((r.status_code, r.url), (302, "/portal/"), url)
        self.assertEqual(self.client.get("/portal/").status_code, 200)

    def test_cliente_nao_baixa_arquivo_de_outra_empresa(self):
        _, [a] = services.publicar(self.outra, [self._arq()], self.equipe, "GUIA")
        self.entrar_cliente()
        self.assertEqual(self.client.get(f"/portal/documentos/{a.pk}/").status_code, 404)

    def test_equipe_nao_entra_no_portal(self):
        self.client.force_login(self.equipe)
        self.assertEqual(self.client.get("/portal/").url, "/")

    def test_acesso_desativado(self):
        self.acesso.ativo = False
        self.acesso.save()
        self.entrar_cliente()
        self.assertEqual(self.client.get("/portal/").url, "/entrar/")

    def test_extensao_proibida(self):
        with self.assertRaises(services.ArquivoRecusado):
            services.publicar(self.padaria, [SimpleUploadedFile("virus.exe", b"MZ")], self.equipe, "OUTROS")
        self.assertEqual(Protocolo.objects.count(), 0)  # nada fica pela metade

    def test_usuarios_do_portal_nao_aparecem_como_responsavel(self):
        self.client.force_login(self.equipe)
        r = self.client.get("/clientes/novo/")
        self.assertNotContains(r, "Maria Silva")

    # ---------------------------------------------------------- fluxo
    def test_publicar_baixar_confirma_protocolo(self):
        prot, [a] = services.publicar(self.padaria, [self._arq()], self.equipe, "GUIA", "DAS 10/2026",
                                      date(2026, 10, 1), date(2026, 11, 20), Decimal("150"))
        self.assertEqual(prot.codigo[-5:], "00001")
        self.assertFalse(prot.confirmado)
        self.entrar_cliente()
        r = self.client.get("/portal/")
        self.assertContains(r, "DAS 10/2026")
        r = self.client.get(f"/portal/documentos/{a.pk}/")
        self.assertEqual(b"".join(r.streaming_content), b"%PDF-1.4 teste")
        prot.refresh_from_db()
        a.refresh_from_db()
        self.assertEqual(prot.confirmacao_forma, "DOWNLOAD")
        self.assertIsNotNone(a.visualizado_em)
        self.assertEqual(prot.confirmado_ip, "127.0.0.1")

    def test_cliente_envia_xml_vai_para_central(self):
        from pathlib import Path
        xml = (Path(__file__).resolve().parents[1] / "xmls" / "exemplos" / "nfe_saida.xml").read_bytes()
        self.entrar_cliente()
        r = self.client.post("/portal/enviar/", {"categoria": "NOTAS", "competencia": "2026-10",
                                                 "arquivos": [SimpleUploadedFile("nota.xml", xml),
                                                              self._arq("extrato.pdf")]})
        self.assertEqual(r.status_code, 302)
        prot = Protocolo.objects.get()
        self.assertEqual((prot.tipo, prot.confirmacao_forma, prot.itens.count()), ("RECEBIMENTO", "ENVIO", 2))
        from xmls.models import Documento
        self.assertEqual(Documento.objects.get().cliente_emitente, self.padaria)
        # equipe vê e processa
        self.client.force_login(self.equipe)
        a = Arquivo.objects.filter(direcao="RECEBIDO").first()
        self.assertContains(self.client.get("/documentos/"), a.titulo)
        self.client.post(f"/documentos/{a.pk}/processar/")
        a.refresh_from_db()
        self.assertEqual(a.processado_por, self.equipe)

    def test_protocolo_manual_e_telas_equipe(self):
        self.client.force_login(self.equipe)
        r = self.client.post("/protocolos/novo/", {
            "cliente": self.padaria.pk, "tipo": "ENTREGA", "meio": "PESSOAL", "responsavel": self.equipe.pk,
            "item_descricao": ["Livro diário 2025", "", "Balanço 2025"], "item_competencia": ["2025", "", "2025"],
            "item_quantidade": ["1", "1", "2"]})
        p = Protocolo.objects.get()
        self.assertEqual(r.url, f"/protocolos/{p.pk}/")
        self.assertEqual(p.itens.count(), 2)
        self.client.post(f"/protocolos/{p.pk}/confirmar/", {"contato_nome": "Maria"})
        p.refresh_from_db()
        self.assertEqual((p.confirmacao_forma, p.contato_nome), ("ASSINATURA", "Maria"))
        for url in ["/documentos/", "/documentos/?aba=enviados", "/documentos/publicar/", "/protocolos/",
                    "/protocolos/?q=2026/00001", f"/protocolos/{p.pk}/", "/acessos/", "/acessos/novo/",
                    f"/acessos/novo/?cliente={self.padaria.pk}", f"/clientes/{self.padaria.pk}/", "/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_criar_acesso_pela_tela_e_nova_senha(self):
        self.client.force_login(self.equipe)
        r = self.client.post("/acessos/novo/", {"nome": "João", "login": "Joao@Ex.com", "clientes": [self.padaria.pk, self.outra.pk]})
        self.assertContains(r, "Senha provisória")
        senha = r.context["senha"]
        self.client.logout()
        self.assertTrue(self.client.login(username="joao@ex.com", password=senha))
        r = self.client.get("/portal/")
        self.assertContains(r, "Outra")  # pode trocar de empresa
        self.client.get(f"/portal/empresa/{self.outra.pk}/")
        self.assertContains(self.client.get("/portal/"), self.outra.documento_formatado)

    def test_portal_honorarios_e_telas(self):
        Cobranca.objects.create(cliente=self.padaria, competencia=date(2026, 10, 1), descricao="Honorário 10/2026",
                                valor=Decimal("650"), vencimento=date(2026, 11, 10), pix_copia_cola="000201TESTE")
        paga = Cobranca.objects.create(cliente=self.padaria, competencia=date(2026, 9, 1), descricao="Honorário 09/2026",
                                       valor=Decimal("650"), vencimento=date(2026, 10, 10), status="PAGA",
                                       data_pagamento=date(2026, 10, 9), valor_pago=Decimal("650"))
        outra = Cobranca.objects.create(cliente=self.outra, competencia=date(2026, 9, 1), descricao="x",
                                        valor=Decimal("1"), vencimento=date(2026, 10, 10), status="PAGA",
                                        data_pagamento=date(2026, 10, 9), valor_pago=Decimal("1"))
        prot, _ = services.publicar(self.padaria, [self._arq()], self.equipe, "FOLHA")
        self.entrar_cliente()
        r = self.client.get("/portal/honorarios/")
        self.assertContains(r, "000201TESTE")
        self.assertContains(r, "<svg")
        self.assertEqual(self.client.get(f"/portal/honorarios/{paga.pk}/recibo/").status_code, 200)
        self.assertEqual(self.client.get(f"/portal/honorarios/{outra.pk}/recibo/").status_code, 404)
        for url in ["/portal/documentos/", "/portal/enviar/", "/portal/protocolos/", f"/portal/protocolos/{prot.pk}/",
                    "/portal/senha/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.client.post(f"/portal/protocolos/{prot.pk}/confirmar/")
        prot.refresh_from_db()
        self.assertEqual(prot.confirmacao_forma, "CLIQUE")

    def test_numeracao_sequencial(self):
        a = Protocolo.objects.create(cliente=self.padaria)
        b = Protocolo.objects.create(cliente=self.padaria)
        self.assertEqual(b.numero, a.numero + 1)
