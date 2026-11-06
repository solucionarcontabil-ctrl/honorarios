import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from cobranca.models import Cliente

from . import parser, services
from .models import Documento

EX = Path(__file__).parent / "exemplos"


def ler(nome):
    return (EX / nome).read_bytes()


def todos_os_exemplos():
    return [(p.name, p.read_bytes()) for p in sorted(EX.glob("*.xml"))]


class ParserTest(TestCase):
    def test_nfe(self):
        d = parser.ler(ler("nfe_saida.xml"))[0][0]
        self.assertEqual((d["modelo"], d["numero"], d["emissao"]), ("NFE", "1234", date(2026, 10, 15)))
        self.assertEqual(d["valor_total"], Decimal("1000.00"))
        self.assertEqual(d["cfops"], {"5102": "800.00", "5405": "200.00"})
        self.assertEqual(len(d["chave"]), 44)

    def test_cte_tomador_remetente(self):
        d = parser.ler(ler("cte.xml"))[0][0]
        self.assertEqual(d["dest_doc"], "11222333000181")  # toma=0 -> remetente

    def test_nfse_varios_padroes(self):
        self.assertEqual(parser.ler(ler("nfse_nacional.xml"))[0][0]["valor_total"], Decimal("2000.00"))
        self.assertEqual(parser.ler(ler("nfse_abrasf.xml"))[0][0]["dest_doc"], "11222333000181")
        sp = parser.ler(ler("nfse_paulistana.xml"))[0]
        self.assertEqual([d["situacao"] for d in sp], ["AUTORIZADA", "CANCELADA"])

    def test_rejeita_lixo_e_xml_malicioso(self):
        with self.assertRaises(parser.XMLNaoReconhecido):
            parser.ler(b"<qualquer><coisa/></qualquer>")
        bomba = b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><l>&b;</l>'
        with self.assertRaises(parser.XMLNaoReconhecido):
            parser.ler(bomba)


class ImportacaoTest(TestCase):
    def setUp(self):
        self.padaria = Cliente.objects.create(nome="Padaria", documento="11222333000181", regime="SIMPLES")
        self.clinica = Cliente.objects.create(nome="Clínica", documento="45723174000110", regime="PRESUMIDO")

    def test_importa_vincula_e_cancela(self):
        lote = services.importar(todos_os_exemplos())
        self.assertEqual(lote.novos, 8)
        self.assertEqual(lote.eventos, 1)
        self.assertEqual(lote.erros, 0)
        self.assertEqual(Documento.objects.get(numero="1235").situacao, "CANCELADA")
        self.assertEqual(services.emitidas(self.padaria).count(), 2)
        self.assertEqual(set(services.recebidas(self.padaria).values_list("numero", flat=True)), {"555", "99", "2026000045"})
        # faturamento: só a NF-e 1234 autorizada (a 1235 foi cancelada)
        self.assertEqual(services.somar(services.faturamento_qs(self.padaria))["total"], Decimal("1000.00"))

    def test_nao_duplica_e_evento_antes_da_nota(self):
        services.importar([("ev.xml", ler("evento_cancelamento.xml"))])
        lote = services.importar([("a.xml", ler("nfe_cancelar.xml")), ("b.xml", ler("nfe_cancelar.xml"))])
        self.assertEqual((lote.novos, lote.repetidos), (1, 1))
        self.assertEqual(Documento.objects.get().situacao, "CANCELADA")

    def test_zip_e_zip_dentro_de_zip(self):
        interno = io.BytesIO()
        with zipfile.ZipFile(interno, "w") as z:
            z.writestr("cte.xml", ler("cte.xml"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("pasta/nfe.xml", ler("nfe_saida.xml"))
            z.writestr("__MACOSX/._nfe.xml", b"lixo")
            z.writestr("leia-me.txt", b"ignorar")
            z.writestr("ctes.zip", interno.getvalue())
            z.writestr("ruim.xml", b"<nao-fiscal/>")
        lote = services.importar([("notas.zip", buf.getvalue())])
        self.assertEqual((lote.novos, lote.erros), (2, 1))

    def test_filial_pela_raiz_e_revincular(self):
        services.importar([("p.xml", ler("nfse_paulistana.xml"))])
        mercadinho = Cliente.objects.create(nome="Mercadinho", documento="60701190000104")
        self.assertEqual(services.revincular(), 1)
        self.assertEqual(services.recebidas(mercadinho).count(), 1)

    def test_rbt12(self):
        services.importar([("n.xml", ler("nfe_saida.xml"))])
        linhas, ano = services.faturamento_12m(self.padaria, date(2026, 11, 1))
        self.assertEqual(linhas[-1]["rbt12"], Decimal("1000.00"))   # nov/26 olha out/25..out/26
        self.assertEqual(linhas[-2]["valor"], Decimal("1000.00"))   # out/26
        self.assertEqual(linhas[-2]["rbt12"], Decimal("0"))          # out/26 olha até set/26
        self.assertEqual(ano, Decimal("1000.00"))


class TelasTest(TestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_superuser("admin", "a@a.com", "senha-muito-forte-1")
        self.client.force_login(self.u)
        self.padaria = Cliente.objects.create(nome="Padaria", documento="11222333000181", regime="SIMPLES")

    def test_envio_e_telas(self):
        arqs = [SimpleUploadedFile(n, c, content_type="text/xml") for n, c in todos_os_exemplos()]
        r = self.client.post("/xml/", {"arquivos": arqs})
        self.assertEqual(r.status_code, 302)
        d = Documento.objects.get(numero="1234")
        pk = self.padaria.pk
        for url in ["/xml/", "/xml/?competencia=2026-10", r.url, "/xml/sem-cliente/", f"/xml/nota/{d.pk}/",
                    f"/xml/cliente/{pk}/?competencia=2026-10", f"/xml/cliente/{pk}/?competencia=2026-10&aba=recebidas",
                    f"/xml/cliente/{pk}/faturamento/?ate=2026-11", f"/clientes/{pk}/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        z = self.client.get(f"/xml/cliente/{pk}/?competencia=2026-10&baixar=zip")
        nomes = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
        self.assertEqual(len([n for n in nomes if n.startswith("NFE/")]), 2)
        self.assertEqual(len([n for n in nomes if n.startswith("EVENTOS/")]), 1)
        x = self.client.get(f"/xml/cliente/{pk}/?competencia=2026-10&baixar=excel")
        self.assertEqual(x["Content-Type"].split(";")[0], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertEqual(self.client.get(f"/xml/nota/{d.pk}/?baixar=1").content, ler("nfe_saida.xml"))
