"""Testes com valores calculados à mão a partir das tabelas legais."""
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from cobranca.models import Cliente
from xmls import parser

from . import services as S
from .models import Apuracao, FolhaMensal, ReceitaMensal

EX = Path(__file__).resolve().parents[1] / "xmls" / "exemplos"


def receitas(cliente, tipo, ate, meses, valor):
    for i in range(1, meses + 1):
        ReceitaMensal.objects.create(cliente=cliente, competencia=S.somar_meses(ate, -i), tipo=tipo, valor=D(valor))


class SimplesTest(TestCase):
    def setUp(self):
        self.c = Cliente.objects.create(nome="Loja", documento="11222333000181", regime="SIMPLES")
        self.comp = date(2026, 10, 1)

    def test_anexo_i_terceira_faixa(self):
        receitas(self.c, "SN_I", self.comp, 12, "50000")
        ReceitaMensal.objects.create(cliente=self.c, competencia=self.comp, tipo="SN_I", valor=D("50000"))
        r = S.calcular_simples(self.c, self.comp)
        self.assertEqual(D(r["rbt12"]), D("600000"))
        l = r["linhas"][0]
        # (600.000 × 9,5% − 13.860) ÷ 600.000 = 7,19%  → 50.000 × 7,19% = 3.595,00
        self.assertEqual((l["faixa"], D(l["aliquota_efetiva"])), (3, D("7.1900")))
        self.assertEqual(D(r["total"]), D("3595.00"))

    def test_fator_r(self):
        receitas(self.c, "SN_FATOR_R", self.comp, 12, "50000")
        ReceitaMensal.objects.create(cliente=self.c, competencia=self.comp, tipo="SN_FATOR_R", valor=D("50000"))
        for i in range(1, 13):
            FolhaMensal.objects.create(cliente=self.c, competencia=S.somar_meses(self.comp, -i), valor=D("15000"))
        r = S.calcular_simples(self.c, self.comp)  # folha 180k / 600k = 30% → Anexo III
        self.assertEqual(r["linhas"][0]["anexo"], "III")
        self.assertEqual(D(r["total"]), D("5280.00"))  # (81.000 − 17.640) ÷ 600.000 = 10,56%
        FolhaMensal.objects.update(valor=D("10000"))   # 120k / 600k = 20% → Anexo V
        r = S.calcular_simples(self.c, self.comp)
        self.assertEqual(r["linhas"][0]["anexo"], "V")
        self.assertEqual(D(r["total"]), D("8925.00"))  # (117.000 − 9.900) ÷ 600.000 = 17,85%

    def test_inicio_de_atividade(self):
        pf = S.perfil(self.c)
        pf.data_inicio_atividade = date(2026, 10, 15)
        pf.save()
        ReceitaMensal.objects.create(cliente=self.c, competencia=self.comp, tipo="SN_I", valor=D("10000"))
        r = S.calcular_simples(self.c, self.comp)
        self.assertTrue(r["proporcional"])
        self.assertEqual(D(r["rbt12"]), D("120000"))   # 10.000 × 12
        self.assertEqual(D(r["total"]), D("400.00"))   # 1ª faixa 4%
        nov = date(2026, 11, 1)
        ReceitaMensal.objects.create(cliente=self.c, competencia=nov, tipo="SN_I", valor=D("30000"))
        r = S.calcular_simples(self.c, nov)
        self.assertEqual(D(r["rbt12"]), D("120000"))   # média dos meses anteriores (10.000) × 12

    def test_alertas_de_limite(self):
        receitas(self.c, "SN_I", self.comp, 12, "350000")  # 4,2 milhões
        ReceitaMensal.objects.create(cliente=self.c, competencia=self.comp, tipo="SN_I", valor=D("1"))
        self.assertTrue(any("sublimite" in a for a in S.calcular_simples(self.c, self.comp)["alertas"]))

    def test_tabelas_conferem_continuidade(self):
        """Da 1ª à 5ª faixa a alíquota efetiva é contínua nas fronteiras (propriedade das tabelas da LC 155).
        Na passagem para a 6ª faixa ela cai de propósito: acima de R$ 3,6 mi o ICMS/ISS saem do DAS."""
        from .tabelas import ANEXOS
        for anexo, faixas in ANEXOS.items():
            for i in range(4):
                lim = faixas[i][0]
                _, _, _, a = S.aliquota_efetiva(anexo, lim)
                _, _, _, b = S.aliquota_efetiva(anexo, lim + D("0.01"))
                self.assertAlmostEqual(float(a), float(b), places=2, msg=f"Anexo {anexo} faixa {i + 1}")


class PresumidoTest(TestCase):
    def setUp(self):
        self.c = Cliente.objects.create(nome="Clínica", documento="45723174000110", regime="PRESUMIDO")

    def test_mensal(self):
        ReceitaMensal.objects.create(cliente=self.c, competencia=date(2026, 10, 1), tipo="LP_SERVICOS", valor=D("100000"))
        r = S.calcular_presumido_mensal(self.c, date(2026, 10, 1))
        self.assertEqual((D(r["pis"]), D(r["cofins"]), D(r["iss"])), (D("650.00"), D("3000.00"), D("5000.00")))

    def test_trimestral_comercio_sem_adicional(self):
        for m in (7, 8, 9):
            ReceitaMensal.objects.create(cliente=self.c, competencia=date(2026, m, 1), tipo="LP_COMERCIO", valor=D("100000"))
        r = S.calcular_presumido_trimestral(self.c, 2026, 3)
        self.assertEqual((D(r["base_irpj"]), D(r["irpj"]), D(r["adicional"])), (D("24000.00"), D("3600.00"), D("0.00")))
        self.assertEqual(D(r["csll"]), D("3240.00"))

    def test_trimestral_servicos_com_adicional_e_retencoes(self):
        for m in (7, 8, 9):
            ReceitaMensal.objects.create(cliente=self.c, competencia=date(2026, m, 1), tipo="LP_SERVICOS", valor=D("100000"))
        r = S.calcular_presumido_trimestral(self.c, 2026, 3, irrf_retido=D("1500"))
        # base 96.000: 15% = 14.400 + adicional (96.000 − 60.000) × 10% = 3.600 → 18.000 − 1.500 IRRF
        self.assertEqual((D(r["adicional"]), D(r["irpj"])), (D("3600.00"), D("16500.00")))
        self.assertEqual(D(r["csll"]), D("8640.00"))
        self.assertTrue(r["quotas"])

    def test_presuncao_propria_do_cliente(self):
        pf = S.perfil(self.c)
        pf.presuncao_irpj = D("16")
        pf.save()
        ReceitaMensal.objects.create(cliente=self.c, competencia=date(2026, 7, 1), tipo="LP_SERVICOS", valor=D("100000"))
        self.assertEqual(D(S.calcular_presumido_trimestral(self.c, 2026, 3)["base_irpj"]), D("16000.00"))


class NotasEReformaTest(TestCase):
    def test_ibs_cbs_no_xml(self):
        xml = (EX / "nfe_saida.xml").read_text().replace(
            "</ICMSTot>", "</ICMSTot><IBSCBSTot><vBCIBSCBS>1000.00</vBCIBSCBS><gIBS><gIBSUF><vIBSUF>1.00</vIBSUF></gIBSUF>"
                          "<vIBS>1.00</vIBS></gIBS><gCBS><vCBS>9.00</vCBS></gCBS></IBSCBSTot>")
        d = parser.ler(xml.encode())[0][0]
        self.assertEqual((d["valor_ibs"], d["valor_cbs"]), (D("1.00"), D("9.00")))
        self.assertEqual(parser.ler((EX / "nfe_saida.xml").read_bytes())[0][0]["valor_ibs"], D("0"))  # sem o grupo

    def test_importar_receitas_e_telas(self):
        from xmls.services import importar
        u = get_user_model().objects.create_superuser("contador", "c@c.com", "senha-muito-forte-1")
        padaria = Cliente.objects.create(nome="Padaria", documento="11222333000181", regime="SIMPLES")
        clinica = Cliente.objects.create(nome="Clínica", documento="45723174000110", regime="PRESUMIDO")
        importar([(p.name, p.read_bytes()) for p in sorted(EX.glob("*.xml"))])
        out = date(2026, 10, 1)
        self.assertEqual(S.importar_receitas_xml(padaria, out), {"SN_I": D("1000.00")})  # 1235 cancelada fica fora
        self.assertEqual(S.importar_receitas_xml(clinica, out), {"LP_SERVICOS": D("2750.00")})  # NFS-e nacional + Paulistana
        manual = ReceitaMensal.objects.get(cliente=padaria, tipo="SN_I")
        manual.valor, manual.origem = D("1500"), "MANUAL"
        manual.save()
        S.importar_receitas_xml(padaria, out)
        manual.refresh_from_db()
        self.assertEqual(manual.valor, D("1500"))  # digitado à mão não é sobrescrito
        self.client.force_login(u)
        for url in ["/fiscal/", "/fiscal/tabelas/", f"/fiscal/{padaria.pk}/", f"/fiscal/{padaria.pk}/simples/?competencia=2026-10",
                    f"/fiscal/{clinica.pk}/presumido/?competencia=2026-09", f"/fiscal/{clinica.pk}/cfop/?competencia=2026-10",
                    f"/fiscal/{clinica.pk}/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.client.post(f"/fiscal/{padaria.pk}/simples/", {"competencia": "2026-10", "acao": "salvar"})
        ap = Apuracao.objects.get(cliente=padaria)
        self.client.post(f"/fiscal/{padaria.pk}/simples/", {"competencia": "2026-10", "acao": "conferir"})
        ap.refresh_from_db()
        self.assertEqual((ap.conferida, ap.conferida_por), (True, u))
        self.client.post(f"/fiscal/{padaria.pk}/", {"parte": "receitas", "r__2026-09__SN_I": "2.345,67", "f__2026-09": "1000"})
        self.assertEqual(ReceitaMensal.objects.get(cliente=padaria, competencia=date(2026, 9, 1)).valor, D("2345.67"))
