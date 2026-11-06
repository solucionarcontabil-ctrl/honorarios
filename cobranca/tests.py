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
