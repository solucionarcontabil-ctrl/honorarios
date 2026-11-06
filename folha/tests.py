"""Valores conferidos à mão com as tabelas oficiais (memória de cálculo nos comentários)."""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase

from cobranca.models import Cliente

from . import calculo as C
from . import services as S
from .models import Dependente, Empregado, Ferias, Folha, Rescisao, TabelaLegal, Variavel


def tab(tipo, ano):
    return TabelaLegal.vigente(tipo, date(ano, 6, 30)).dados


class CalculoTest(TestCase):
    def test_inss_2025(self):
        # 1518×7,5% + 1275,88×9% + 206,12×12% = 113,85 + 114,8292 + 24,7344 = 253,41
        self.assertEqual(C.inss_empregado(D("3000"), tab("INSS", 2025))[0], D("253.41"))
        # + 1396,95×12% + 809,17×14% → 509,60
        self.assertEqual(C.inss_empregado(D("5000"), tab("INSS", 2025))[0], D("509.60"))
        teto = C.inss_empregado(D("20000"), tab("INSS", 2025))[0]
        self.assertEqual(teto, C.inss_empregado(D("8157.41"), tab("INSS", 2025))[0])
        self.assertEqual(C.inss_contribuinte_individual(D("20000"), tab("INSS", 2025)), D("897.32"))  # 11% do teto

    def test_irrf_simplificado_e_legais(self):
        t = tab("IRRF", 2025)
        # 3.000: simplificado 607,20 > INSS 253,41 → base 2.392,80 → isento
        self.assertEqual(C.irrf(D("3000"), D("253.41"), 0, 0, t)[0], D("0"))
        # 5.000: base 5.000 − 607,20 = 4.392,80 × 22,5% − 675,49 = 312,89
        self.assertEqual(C.irrf(D("5000"), D("509.60"), 0, 0, t)[0], D("312.89"))
        # com 2 dependentes: deduções legais 509,60 + 379,18 = 888,78 > 607,20 → base 4.111,22 × 22,5% − 675,49 = 249,53
        self.assertEqual(C.irrf(D("5000"), D("509.60"), 2, 0, t)[0], D("249.53"))

    def test_irrf_2026_com_reducao(self):
        t = TabelaLegal.vigente("IRRF", date(2026, 3, 1)).dados
        self.assertEqual(C.irrf(D("5000"), D("501.51"), 0, 0, t)[0], D("0"))  # isento até 5.000
        # 7.000: base 7.000 − 781,51 = 6.218,49 × 27,5% − 908,73 = 801,35; redução 978,62 − 0,133145×7.000 = 46,61
        self.assertEqual(C.irrf(D("7000"), D("781.51"), 0, 0, t)[0], D("754.74"))
        self.assertEqual(C.irrf(D("9000"), D("900"), 0, 0, t)[1], D("8100.00"))

    def test_dias_avos_aviso(self):
        self.assertEqual(C.dias_mes_comercial(date(2026, 1, 1), date(2025, 1, 1)), 30)
        self.assertEqual(C.dias_mes_comercial(date(2026, 2, 1), date(2025, 1, 1)), 30)
        self.assertEqual(C.dias_mes_comercial(date(2026, 3, 1), date(2026, 3, 16)), 16)  # 16 a 31 = 16 dias corridos
        self.assertEqual(C.dias_mes_comercial(date(2026, 2, 1), date(2025, 1, 1), date(2026, 2, 28)), 30)
        self.assertEqual(C.dias_mes_comercial(date(2026, 3, 1), date(2025, 1, 1), date(2026, 3, 10)), 10)
        self.assertEqual(C.avos_13(date(2025, 3, 20), date(2025, 12, 31), 2025), 9)   # março com 12 dias não conta
        self.assertEqual(C.dias_aviso_previo(date(2023, 3, 1), date(2025, 6, 10)), 36)
        self.assertEqual(C.dias_aviso_previo(date(2000, 1, 1), date(2025, 6, 10)), 90)
        self.assertEqual(C.avos_ferias(date(2025, 3, 1), date(2025, 7, 16)), 5)


class Base(TestCase):
    def setUp(self):
        TabelaLegal.objects.update(conferida=True)
        self.u = get_user_model().objects.create_superuser("contador", "c@c.com", "senha-muito-forte-1")
        self.c = Cliente.objects.create(nome="Padaria", documento="11222333000181", regime="SIMPLES")
        self.cfg = S.config(self.c)

    def emp(self, **kw):
        dados = dict(cliente=self.c, matricula=S.proxima_matricula(self.c), nome="Maria", cpf="52998224725",
                     admissao=date(2024, 1, 2), salario=D("3000"))
        dados.update(kw)
        return Empregado.objects.create(**dados)


class FolhaTest(Base):
    def test_folha_simples(self):
        e = self.emp()
        f = S.calcular_folha(self.c, date(2025, 6, 1), usuario=self.u)
        h = f.holerites.get(empregado=e)
        self.assertEqual((h.proventos, h.inss, h.irrf, h.liquido, h.fgts), (D("3000"), D("253.41"), D("0"), D("2746.59"), D("240.00")))
        self.assertEqual(D(f.resumo["cpp"]), D("0"))  # Simples: CPP no DAS

    def test_horas_extras_dsr_faltas(self):
        e = self.emp(salario=D("2200"))  # hora = 10
        comp = date(2025, 6, 1)  # junho/2025: 25 dias úteis (seg-sáb), 5 domingos/feriados (Corpus Christi 19/06)
        Variavel.objects.create(empregado=e, competencia=comp, tipo="HE50", quantidade=D("10"))
        Variavel.objects.create(empregado=e, competencia=comp, tipo="FALTAS", quantidade=D("1"))
        h = S.calcular_folha(self.c, comp).holerites.get()
        itens = {i["codigo"]: D(i["valor"]) for i in h.itens}
        self.assertEqual(itens["HE50"], D("150.00"))
        uteis, dsr = C.dias_uteis_e_dsr(comp)
        self.assertEqual((uteis, dsr), (24, 6))
        self.assertEqual(itens["DSR"], D("37.50"))  # 150 ÷ 24 × 6
        self.assertEqual(itens["FALTAS"], D("73.33"))

    def test_hora_extra_com_insalubridade(self):
        e = self.emp(salario=D("2200"), insalubridade=20)  # SM 2025 1.518 → 303,60
        Variavel.objects.create(empregado=e, competencia=date(2025, 6, 1), tipo="HE50", quantidade=D("10"))
        h = S.calcular_folha(self.c, date(2025, 6, 1)).holerites.get()
        # hora = (2.200 + 303,60) ÷ 220 = 11,38 → 10h × 1,5 = 170,70
        self.assertEqual({i["codigo"]: D(i["valor"]) for i in h.itens}["HE50"], D("170.70"))

    def test_prolabore_e_patronal(self):
        self.c.regime = "PRESUMIDO"
        self.c.save()
        cfg = S.config(self.c)
        cfg.cpp, cfg.rat, cfg.terceiros = D("20"), D("2"), D("5.8")
        cfg.save()
        self.emp(tipo="PROLABORE", nome="Sócio", salario=D("5000"))
        self.emp(nome="Empregado", salario=D("3000"))
        f = S.calcular_folha(self.c, date(2025, 6, 1))
        socio = f.holerites.get(empregado__nome="Sócio")
        self.assertEqual((socio.inss, socio.fgts), (D("550.00"), D("0")))
        self.assertEqual(D(f.resumo["cpp"]), D("1600.00"))   # 20% de 8.000
        self.assertEqual(D(f.resumo["rat"]), D("60.00"))     # 2% × FAP 1 de 3.000
        self.assertEqual(D(f.resumo["terceiros"]), D("174.00"))

    def test_salario_familia_vt_pensao_dependentes(self):
        e = self.emp(salario=D("1600"), vt_custo_mensal=D("200"), pensao_percentual=D("10"))
        Dependente.objects.create(empregado=e, nome="Filho", nascimento=date(2020, 1, 1))
        h = S.calcular_folha(self.c, date(2025, 6, 1)).holerites.get()
        itens = {i["codigo"]: D(i["valor"]) for i in h.itens}
        self.assertEqual(itens["SALFAMILIA"], D("65.00"))
        self.assertEqual(itens["VT"], D("96.00"))  # 6% de 1.600 (menor que o custo de 200)
        inss = itens["INSS"]                        # 113,85 + 82×9% = 121,23
        self.assertEqual(inss, D("121.23"))
        self.assertEqual(itens["PENSAO"], D("154.38"))  # 10% × (1.665 − 121,23 − 0)

    def test_adiantamento_descontado(self):
        e = self.emp(recebe_adiantamento=True)
        S.calcular_folha(self.c, date(2025, 6, 1), "ADIANTAMENTO")
        h = S.calcular_folha(self.c, date(2025, 6, 1)).holerites.get(empregado=e)
        self.assertEqual({i["codigo"]: D(i["valor"]) for i in h.itens}["ADIANT"], D("1200.00"))

    def test_fechar_exige_tabela_conferida_e_alimenta_fator_r(self):
        self.emp()
        f = S.calcular_folha(self.c, date(2026, 3, 1))
        TabelaLegal.objects.filter(vigencia_inicio__year=2026).update(conferida=False)
        with self.assertRaises(S.ErroFolha):
            S.fechar(f, self.u)
        TabelaLegal.objects.update(conferida=True)
        S.fechar(f, self.u)
        from fiscal.models import FolhaMensal
        self.assertEqual(FolhaMensal.objects.get(cliente=self.c, competencia=date(2026, 3, 1)).valor, D("3240.00"))


class FeriasDecimoTest(Base):
    def test_ferias(self):
        e = self.emp(admissao=date(2024, 1, 2))
        f = Ferias.objects.create(empregado=e, aquisitivo_inicio=date(2024, 1, 2), aquisitivo_fim=date(2025, 1, 1),
                                  gozo_inicio=date(2025, 6, 2), dias=30)
        v = S.calcular_ferias(f).valores
        # 3.000 + 1/3 = 4.000; INSS 373,41; IRRF (simplificado) base 3.392,80 × 15% − 394,16 = 114,76
        self.assertEqual((D(v["inss"]), D(v["irrf"]), D(v["liquido"])), (D("373.41"), D("114.76"), D("3511.83")))
        # na folha de junho: só 0 dia de salário? (gozo 02/06 a 01/07: 29 dias em junho) → salário de 1 dia
        h = S.calcular_folha(self.c, date(2025, 6, 1)).holerites.get()
        self.assertEqual({i["codigo"]: D(i["valor"]) for i in h.itens}["SALARIO"], D("100.00"))

    def test_ferias_invalida(self):
        e = self.emp()
        f = Ferias(empregado=e, aquisitivo_inicio=date(2024, 1, 2), aquisitivo_fim=date(2025, 1, 1),
                   gozo_inicio=date(2025, 6, 2), dias=25, abono_dias=10)
        with self.assertRaises(S.ErroFolha):
            S.calcular_ferias(f)

    def test_decimo(self):
        self.emp(admissao=date(2024, 1, 2))
        h1 = S.calcular_folha(self.c, date(2025, 11, 1), "DECIMO1").holerites.get()
        self.assertEqual((h1.liquido, h1.fgts), (D("1500.00"), D("120.00")))
        h2 = S.calcular_folha(self.c, date(2025, 12, 1), "DECIMO2").holerites.get()
        # 3.000 − 1.500 − INSS 253,41 − IRRF (2.746,59 × 7,5% − 182,16 = 23,83)
        self.assertEqual((h2.inss, h2.irrf, h2.liquido, h2.fgts), (D("253.41"), D("23.83"), D("1222.76"), D("120.00")))


class RescisaoTest(Base):
    def test_sem_justa_causa(self):
        e = self.emp(admissao=date(2023, 3, 1))
        r = Rescisao.objects.create(empregado=e, tipo="SEM_JUSTA_CAUSA", data_aviso=date(2025, 6, 10),
                                    data_desligamento=date(2025, 6, 10), aviso="INDENIZADO", saldo_fgts=D("5000"))
        v = S.calcular_rescisao(r).valores
        it = {i["codigo"]: D(i["valor"]) for i in v["itens"]}
        self.assertEqual(it["SALDO"], D("1000.00"))
        self.assertEqual(it["AVISO"], D("3600.00"))        # 36 dias
        self.assertEqual(it["13_PROP"], D("1750.00"))      # 7/12 (projeção até 16/07)
        self.assertEqual(it["FERIAS_VENC"], D("6000.00"))  # 2 períodos vencidos
        self.assertEqual(it["FERIAS_PROP"], D("1250.00"))  # 5/12
        self.assertEqual(it["TERCO_PROP"], D("416.67"))
        self.assertEqual((it["INSS"], it["INSS13"]), (D("75.00"), D("134.73")))
        self.assertEqual(D(v["fgts_mes"]), D("508.00"))    # 8% de (1.000 + 3.600 + 1.750)
        self.assertEqual(D(v["multa_fgts"]), D("2203.20"))  # 40% de (5.000 + 508)
        self.assertEqual(D(v["liquido"]), D("15806.94"))
        self.assertTrue(v["seguro_desemprego"])

    def test_justa_causa_e_acordo(self):
        e = self.emp(admissao=date(2023, 3, 1))
        r = Rescisao(empregado=e, tipo="JUSTA_CAUSA", data_aviso=date(2025, 6, 10), data_desligamento=date(2025, 6, 10),
                     aviso="DISPENSADO")
        r.save()
        it = {i["codigo"] for i in S.calcular_rescisao(r).valores["itens"]}
        self.assertNotIn("13_PROP", it)
        self.assertNotIn("FERIAS_PROP", it)
        r.tipo, r.aviso, r.saldo_fgts = "ACORDO", "INDENIZADO", D("5000")
        v = S.calcular_rescisao(r).valores
        self.assertEqual({i["codigo"]: D(i["valor"]) for i in v["itens"]}["AVISO"], D("1800.00"))  # metade de 36 dias
        self.assertEqual(D(v["multa_pct"]), D("20"))


class TelasTest(Base):
    def test_fluxo_completo_pelas_telas(self):
        self.client.force_login(self.u)
        cid = self.c.pk
        r = self.client.post(f"/folha/{cid}/empregado/novo/", {
            "nome": "João Teste", "cpf": "529.982.247-25", "tipo": "EMPREGADO", "admissao": "2024-01-02",
            "contrato": "INDETERMINADO", "salario": "2.200,00", "horas_mensais": "220", "insalubridade": "0",
            "vt_custo_mensal": "0", "pensao_percentual": "0", "pensao_base": "LIQUIDO"})
        self.assertEqual(r.status_code, 302, r.content[:500])
        e = Empregado.objects.get(nome="João Teste")
        self.assertEqual((e.matricula, e.salario, e.cpf), (1, D("2200"), "52998224725"))
        self.client.post(f"/folha/{cid}/empregado/{e.pk}/", {"parte": "dependente", "dep-nome": "Ana", "dep-nascimento": "2019-05-01",
                                                             "dep-parentesco": "Filha", "dep-ir": "on", "dep-salario_familia": "on"})
        self.assertEqual(e.dependentes.count(), 1)
        self.client.post(f"/folha/{cid}/variaveis/", {"competencia": "2025-06", f"v_{e.pk}_HE50": "10"})
        self.assertEqual(Variavel.objects.get().quantidade, D("10"))
        r = self.client.post(f"/folha/{cid}/calcular/", {"competencia": "2025-06", "tipo": "MENSAL"})
        f = Folha.objects.get()
        self.assertEqual(r.url, f"/folha/processamento/{f.pk}/")
        pdfr = self.client.get(f"/folha/processamento/{f.pk}/holerites.pdf")
        self.assertEqual(pdfr["Content-Type"], "application/pdf")
        self.assertTrue(pdfr.content.startswith(b"%PDF"))
        self.client.post(f"/folha/processamento/{f.pk}/fechar/")
        f.refresh_from_db()
        self.assertTrue(f.fechada)
        self.client.post(f"/folha/processamento/{f.pk}/portal/")
        from portal.models import Arquivo
        self.assertEqual(Arquivo.objects.get().categoria, "FOLHA")
        r = self.client.post(f"/folha/{cid}/ferias/nova/", {"empregado": e.pk, "aquisitivo_inicio": "2024-01-02",
                                                            "aquisitivo_fim": "2025-01-01", "gozo_inicio": "2025-08-04",
                                                            "dias": "20", "abono_dias": "10", "medias": "0"})
        fer = Ferias.objects.get()
        self.assertEqual(r.url, f"/folha/{cid}/ferias/{fer.pk}/")
        r = self.client.post(f"/folha/{cid}/rescisao/{e.pk}/", {"tipo": "PEDIDO", "data_aviso": "2025-09-01",
                                                               "data_desligamento": "2025-09-30", "aviso": "TRABALHADO",
                                                               "saldo_fgts": "0", "medias": "0", "outros_descontos": "0"})
        self.assertEqual(r.status_code, 302)
        self.client.post(f"/folha/{cid}/rescisao/{e.pk}/", {"acao": "confirmar"})
        e.refresh_from_db()
        self.assertFalse(e.ativo)
        for url in ["/folha/", "/folha/tabelas/", f"/folha/{cid}/", f"/folha/{cid}/?todos=1", f"/folha/{cid}/configuracao/",
                    f"/folha/{cid}/empregado/{e.pk}/", f"/folha/{cid}/variaveis/?competencia=2025-06",
                    f"/folha/processamento/{f.pk}/", f"/folha/{cid}/ferias/{fer.pk}/", f"/folha/{cid}/rescisao/{e.pk}/",
                    f"/clientes/{cid}/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_editar_tabela_volta_para_conferir(self):
        self.client.force_login(self.u)
        t = TabelaLegal.objects.get(tipo="SALARIO_MINIMO", vigencia_inicio=date(2026, 1, 1))
        self.client.post("/folha/tabelas/", {"id": t.pk, "vigencia_inicio": "2026-01-01", "fonte": "Decreto X",
                                             "dados": '{"valor": "1621.00"}'})
        t.refresh_from_db()
        self.assertFalse(t.conferida)
        self.client.post("/folha/tabelas/", {"id": t.pk, "acao": "conferir"})
        t.refresh_from_db()
        self.assertEqual((t.conferida, t.conferida_por), (True, self.u))
