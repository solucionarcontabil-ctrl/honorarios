from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from . import asaas, pix, services
from .models import Cliente, Cobranca, Configuracao, Contrato
from .validators import cnpj_valido, cpf_valido


class ValidadoresTest(TestCase):
    def test_documentos(self):
        self.assertTrue(cnpj_valido("11222333000181"))
        self.assertFalse(cnpj_valido("11222333000182"))
        self.assertTrue(cpf_valido("52998224725"))
        self.assertFalse(cpf_valido("11111111111"))


class PixTest(TestCase):
    def test_crc_referencia(self):
        # vetor conhecido do CRC16-CCITT-FALSE
        self.assertEqual(pix.crc16("123456789"), "29B1")

    def test_payload(self):
        p = pix.gerar_payload("fulano@exemplo.com", "Fulano de Tal", "São Paulo", Decimal("123.45"), "HON1")
        self.assertTrue(p.startswith("000201"))
        self.assertIn("br.gov.bcb.pix", p)
        self.assertIn("5406123.45", p)
        self.assertIn("6009SAO PAULO", p)
        self.assertEqual(p[-4:], pix.crc16(p[:-4]))


class BaseTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "senha-muito-forte-1")
        self.cliente = Cliente.objects.create(nome="Padaria", documento="11.222.333/0001-81", whatsapp="(11) 99999-8888")
        self.contrato = Contrato.objects.create(
            cliente=self.cliente, valor_mensal=Decimal("500.00"), dia_vencimento=31,
            data_inicio=date(2024, 1, 1), mes_reajuste=1, cobra_13=True, mes_13=12,
        )


class GeracaoTest(BaseTest):
    def test_gera_sem_duplicar_e_13(self):
        criadas = services.gerar_mensalidades(date(2025, 12, 1), self.user)
        self.assertEqual({c.tipo for c in criadas}, {"MENSAL", "13"})
        # vencimento no mês seguinte, dia 31 -> 31/01
        self.assertEqual(criadas[0].vencimento, date(2026, 1, 31))
        self.assertEqual(services.gerar_mensalidades(date(2025, 12, 1), self.user), [])

    def test_dia_31_em_fevereiro(self):
        c = services.gerar_mensalidades(date(2026, 1, 1))[0]
        self.assertEqual(c.vencimento, date(2026, 2, 28))

    def test_contrato_futuro_nao_gera(self):
        self.assertEqual(services.gerar_mensalidades(date(2023, 5, 1)), [])


class EncargosTest(BaseTest):
    def test_multa_e_juros(self):
        c = Cobranca.objects.create(cliente=self.cliente, contrato=self.contrato, competencia=date(2026, 1, 1),
                                    descricao="x", valor=Decimal("1000"), vencimento=date(2026, 2, 10))
        # 30 dias: 2% multa + 1% juros
        self.assertEqual(c.encargos(hoje=date(2026, 3, 12)), Decimal("30.00"))
        self.assertEqual(c.encargos(hoje=date(2026, 2, 10)), Decimal("0.00"))


class ReajusteTest(BaseTest):
    def test_aplicar(self):
        novo = services.aplicar_reajuste(self.contrato, Decimal("4.5"), "01/2025 a 12/2025", self.user)
        self.assertEqual(novo, Decimal("522.50"))
        self.assertEqual(self.contrato.reajustes.count(), 1)

    def test_pendente(self):
        self.assertTrue(self.contrato.reajuste_pendente(date(2026, 1, 15)))
        self.assertFalse(self.contrato.reajuste_pendente(date(2026, 2, 15)))
        self.contrato.ultimo_reajuste = date(2026, 1, 5)
        self.assertFalse(self.contrato.reajuste_pendente(date(2026, 1, 15)))

    def test_ipca_acumulado(self):
        dados = [("01/x", Decimal("0.5"))] * 12
        with mock.patch.object(services, "_bcb", return_value=dados):
            perc, periodo = services.indice_acumulado_12m("IPCA", date(2026, 1, 1))
        self.assertEqual(perc, Decimal("6.1678"))
        self.assertEqual(periodo, "01/2025 a 12/2025")


@override_settings(ASAAS_WEBHOOK_TOKEN="tok123")
class WebhookTest(BaseTest):
    def test_baixa_automatica(self):
        c = Cobranca.objects.create(cliente=self.cliente, competencia=date(2026, 1, 1), descricao="x",
                                    valor=Decimal("500"), vencimento=date(2026, 2, 10), gateway_id="pay_1")
        corpo = {"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_1", "value": 500, "billingType": "PIX", "paymentDate": "2026-02-09"}}
        r = self.client.post("/webhooks/asaas/", corpo, content_type="application/json")
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/webhooks/asaas/", corpo, content_type="application/json", headers={"asaas-access-token": "tok123"})
        self.assertEqual(r.status_code, 200)
        c.refresh_from_db()
        self.assertEqual(c.status, Cobranca.PAGA)
        self.assertEqual(c.forma_recebimento, "PIX")


class TelasTest(BaseTest):
    def test_todas_as_telas_abrem(self):
        self.client.force_login(self.user)
        services.gerar_mensalidades(date(2025, 12, 1), self.user)
        c = Cobranca.objects.first()
        with mock.patch.object(services, "_bcb", side_effect=services.IndiceIndisponivel("offline")):
            for url in ["/", "/clientes/", f"/clientes/{self.cliente.pk}/", "/clientes/novo/",
                        f"/clientes/{self.cliente.pk}/honorario/novo/", "/cobrancas/", "/cobrancas/?situacao=todas",
                        "/cobrancas/nova/", f"/cobrancas/{c.pk}/", "/gerar/", "/inadimplencia/",
                        "/reajustes/?mes=2026-01", "/configuracoes/", "/cobrancas/?exportar=csv"]:
                self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_login_obrigatorio(self):
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_baixa_manual_e_recibo(self):
        self.client.force_login(self.user)
        c = services.gerar_mensalidades(date(2026, 1, 1))[0]
        r = self.client.post(f"/cobrancas/{c.pk}/baixa/", {"data_pagamento": "2026-02-05", "valor_pago": "500,00", "forma": "PIX"})
        self.assertEqual(r.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.status, Cobranca.PAGA)
        self.assertEqual(c.baixado_por, self.user)
        self.assertEqual(self.client.get(f"/cobrancas/{c.pk}/recibo/").status_code, 200)

    def test_pix_proprio(self):
        self.client.force_login(self.user)
        cfg = Configuracao.atual()
        cfg.chave_pix, cfg.nome_recebedor_pix = "11222333000181", "ESCRITORIO"
        cfg.save()
        c = services.gerar_mensalidades(date(2026, 1, 1))[0]
        self.client.post(f"/cobrancas/{c.pk}/pix/")
        c.refresh_from_db()
        self.assertTrue(c.pix_copia_cola.startswith("000201"))
        self.assertIn("<svg", self.client.get(f"/cobrancas/{c.pk}/").content.decode())

    def test_mensagem_whatsapp(self):
        c = services.gerar_mensalidades(date(2026, 1, 1))[0]
        self.assertIn("wa.me/5511999998888", services.link_whatsapp(c))
        self.assertIn("janeiro/2026", services.montar_mensagem(c))


class ImportacaoDominioTest(TestCase):
    """Relatório 'Empresas — dados cadastrais' do Domínio (dados fictícios no mesmo formato)."""

    def ficha(self, codigo, razao, doc, email="", inicio="10/02/2020"):
        v = [""] * 21

        def l(a, b="", c="", d=""):
            row = list(v)
            row[0], row[7], row[12], row[15] = a, b, c, d
            return row
        return [l("EMPRESAS"), l("DADOS CADASTRAIS"), l("Código:", float(codigo), "Data da inscrição:", "01/01/2020"),
                l("Razão social:", razao, "Natureza Jurídica:", "Sociedade Empresária Limitada"),
                l("Nome fantasia:", razao.split()[0], "Contador:", "FULANO DE TAL"),
                l("Endereço:", "RUA A", "Início atividades:", inicio), l("Número:", "10"), l("Município:", "SAO PAULO"),
                l("UF:", "SP"), l("CEP:", "01001000"), l("Telefone:", "(11) 2222-3333"), l("E-mail:", email),
                l("CNPJ/CPF/CEI/CAEPF:", doc), l("Insc. municipal:", "1.234.567-8", "CNAE 2.3:", "5611-2/01"),
                l("QUADRO SOCIETÁRIO"), l("MÓDULOS UTILIZADOS"), ["Módulos:", "Contabilidade, Folha"] + [""] * 19]

    def test_fichas_viram_clientes(self):
        from datetime import date
        self.fulano = get_user_model().objects.create_user("fulano", first_name="Fulano", last_name="de Tal")

        from fiscal.models import PerfilFiscal

        from .importacao import fichas_para_tabela, importar, mapear_cabecalho, normalizar_documento
        linhas = [[""] * 21] * 5 + self.ficha(12, "PADARIA PAO QUENTE LTDA", "11.222.333/0001-81", "a@a.com") \
            + self.ficha(15, "CLINICA SORRISO", "45.723.174/0001-10")
        tabela = fichas_para_tabela(linhas)
        self.assertEqual(len(tabela), 3)
        mapa = mapear_cabecalho(tabela[0])
        self.assertEqual(normalizar_documento(tabela[1][mapa["documento"]]), "11222333000181")
        # passa pelo mesmo caminho da tela (analisar com o conteúdo já convertido em linhas)
        from . import importacao
        orig = importacao.ler_linhas
        importacao.ler_linhas = lambda n, c: linhas
        try:
            analisadas, mapa, erro = importacao.analisar("empresas.xls", b"")
        finally:
            importacao.ler_linhas = orig
        self.assertIsNone(erro)
        self.assertEqual([a["situacao"] for a in analisadas], ["novo", "novo"])
        importar(analisadas)
        p = Cliente.objects.get(documento="11222333000181")
        self.assertEqual((p.regime, p.cidade, p.cep, p.inscricao_municipal), ("OUTRO", "SAO PAULO", "01001000", "1.234.567-8"))
        self.assertEqual(p.responsavel, self.fulano)  # "Contador: FULANO DE TAL" → usuário Fulano de Tal
        self.assertIn("CNAE 2.3 5611-2/01", p.observacoes)
        self.assertIn("Código 12", p.observacoes)
        self.assertEqual(PerfilFiscal.objects.get(cliente=p).data_inicio_atividade, date(2020, 2, 10))

    def test_completar_em_lote(self):
        from obrigacoes.models import ClienteObrigacao
        u = get_user_model().objects.create_superuser("contador", "c@c.com", "senha-muito-forte-1")
        c = Cliente.objects.create(nome="Padaria", documento="11222333000181", regime="OUTRO")
        self.client.force_login(u)
        self.assertContains(self.client.get("/clientes/completar/"), "Padaria")
        self.client.post("/clientes/completar/", {"ids": [c.pk], f"regime_{c.pk}": "SIMPLES", f"resp_{c.pk}": u.pk,
                                                  f"func_{c.pk}": "on", f"valor_{c.pk}": "1.250,00", f"dia_{c.pk}": "5",
                                                  "aplicar_obrigacoes": "on"})
        c.refresh_from_db()
        self.assertEqual((c.regime, c.responsavel, c.tem_funcionarios), ("SIMPLES", u, True))
        ct = c.contratos.get()
        self.assertEqual((ct.valor_mensal, ct.dia_vencimento), (Decimal("1250.00"), 5))
        self.assertTrue(ClienteObrigacao.objects.filter(cliente=c, obrigacao__sigla="ESOCIAL").exists())
        self.assertNotContains(self.client.get("/clientes/completar/"), "11.222.333/0001-81")  # saiu dos incompletos


class ImportacaoRegimeTest(TestCase):
    """Planilha de regime: cabeçalho 'CNPJ / CPF', regime por cliente, conferências de coerência."""

    def planilha(self, linhas):
        import io as _io

        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["Regime tributário por empresa"])
        ws.append([])
        ws.append(["Código", "Razão social", "CNPJ / CPF", "Natureza jurídica", "Município / UF", "CNAE", "Atividade",
                    "Regime tributário"])
        for l in linhas:
            ws.append(l)
        buf = _io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_atualiza_outro_e_barra_incoerencias(self):
        from .importacao import analisar, importar
        Cliente.objects.create(nome="Padaria", documento="11222333000181", regime="OUTRO")
        Cliente.objects.create(nome="Clínica", documento="45723174000110", regime="REAL")  # já definido: não mexe
        Cliente.objects.create(nome="Associação X", documento="52998224725", regime="PF")
        Cliente.objects.create(nome="Associação Y", documento="60701190000104", regime="OUTRO")
        Cliente.objects.create(nome="Associação Z", documento="33000167000101", regime="OUTRO")
        arq = self.planilha([
            [1, "PADARIA", "11.222.333/0001-81", "Sociedade Empresária Limitada", "SP", "", "", "Simples Nacional"],
            [2, "CLINICA", "45.723.174/0001-10", "Sociedade Simples Limitada", "SP", "", "", "Lucro Presumido"],
            [3, "ASSOC X", "529.982.247-25", "Associação Privada", "SP", "", "", "Simples Nacional"],
            [4, "ASSOC Y", "60.701.190/0001-04", "Associação Privada", "SP", "", "", "Simples Nacional"],
            [5, "ASSOC Z", "33.000.167/0001-01", "Associação Privada", "SP", "", "", "Lucro Presumido"]])
        linhas, mapa, erro = analisar("regime.xlsx", arq)
        self.assertIsNone(erro)
        motivos = {l["dados"]["documento"]: l["motivo"] for l in linhas}
        self.assertIn("CPF não pode", motivos["52998224725"])
        self.assertIn("não pode optar pelo Simples", motivos["60701190000104"])
        self.assertIn("imune/isenta", motivos["33000167000101"])
        importar(linhas)
        reg = dict(Cliente.objects.values_list("documento", "regime"))
        self.assertEqual(reg, {"11222333000181": "SIMPLES", "45723174000110": "REAL", "52998224725": "PF",
                               "60701190000104": "OUTRO", "33000167000101": "PRESUMIDO"})
        self.assertTrue([l for l in linhas if l.get("regime_aplicado")])


class LoginTest(TestCase):
    def test_login_ignora_maiusculas_e_espacos(self):
        get_user_model().objects.create_user("william", password="senha-muito-forte-1")
        self.assertTrue(self.client.login(username=" William ", password="senha-muito-forte-1"))
        self.assertFalse(self.client.login(username="william", password="errada"))

    def test_redefinir_senha(self):
        from unittest import mock

        from django.core.management import call_command
        u = get_user_model().objects.create_user("william", password="senha-antiga-12345")
        with mock.patch("builtins.input", return_value="WILLIAM"), \
                mock.patch("getpass.getpass", return_value="senha-nova-forte-987"):
            call_command("redefinir_senha", stdout=open("/dev/null", "w"))
        u.refresh_from_db()
        self.assertTrue(u.check_password("senha-nova-forte-987"))
