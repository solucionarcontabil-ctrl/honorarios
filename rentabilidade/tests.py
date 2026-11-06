from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from cobranca.models import Cliente, Cobranca

from . import services
from .models import Apontamento, Membro


class RentabilidadeTest(TestCase):
    def setUp(self):
        U = get_user_model()
        self.filho = U.objects.create_superuser("filho", "f@f.com", "senha-muito-forte-1")
        self.pai = U.objects.create_user("pai", "p@p.com", "senha-muito-forte-1")
        Membro.objects.create(usuario=self.filho, custo_hora=Decimal("60"))
        Membro.objects.create(usuario=self.pai, custo_hora=Decimal("90"))
        self.padaria = Cliente.objects.create(nome="Padaria", documento="11222333000181")
        self.clinica = Cliente.objects.create(nome="Clínica", documento="45723174000110")
        for c, v in ((self.padaria, "500"), (self.clinica, "2000")):
            Cobranca.objects.create(cliente=c, competencia=date(2026, 10, 1), descricao="h", valor=Decimal(v),
                                    vencimento=date(2026, 11, 10))

    def test_parse_tempo(self):
        for t, m in (("90", 90), ("1:30", 90), ("1h30", 90), ("2h", 120), ("1,5h", 90), ("0:45", 45), ("abc", None)):
            self.assertEqual(services.parse_tempo(t), m, t)

    def test_relatorio(self):
        Apontamento.objects.create(usuario=self.filho, cliente=self.padaria, data=date(2026, 10, 5), minutos=300)  # 5h*60=300
        Apontamento.objects.create(usuario=self.pai, cliente=self.padaria, data=date(2026, 10, 6), minutos=120)    # 2h*90=180
        Apontamento.objects.create(usuario=self.filho, cliente=self.clinica, data=date(2026, 10, 6), minutos=60)   # 60
        Apontamento.objects.create(usuario=self.filho, cliente=None, data=date(2026, 10, 6), minutos=30)
        linhas, tot, _ = services.relatorio(date(2026, 10, 1), date(2026, 10, 1))
        padaria = next(l for l in linhas if l["cliente"] == self.padaria)
        self.assertEqual(padaria["custo"], Decimal("480.00"))
        self.assertEqual(padaria["margem"], Decimal("20.00"))
        # margem alvo 30%: 480 / 0,7 = 685,71
        self.assertEqual(padaria["sugerido"], Decimal("685.71"))
        clinica = next(l for l in linhas if l["cliente"] == self.clinica)
        self.assertIsNone(clinica["sugerido"])
        self.assertEqual(linhas[0]["cliente"], self.padaria)  # pior margem primeiro
        self.assertEqual((tot["interno_min"], tot["custo"]), (30, Decimal("540.00")))

    def test_cronometro_e_telas(self):
        self.client.force_login(self.filho)
        self.client.post("/horas/cronometro/iniciar/", {"cliente": self.padaria.pk, "atividade": "FISCAL"})
        a = Apontamento.objects.get(em_andamento=True)
        self.assertContains(self.client.get("/"), "Cronômetro rodando")
        self.client.post("/horas/cronometro/parar/")
        a.refresh_from_db()
        self.assertFalse(a.em_andamento)
        self.assertEqual(a.minutos, 1)
        self.client.post("/horas/lancar/", {"cliente": self.clinica.pk, "tempo": "1:15", "atividade": "CONTABIL", "data": "2026-10-10"})
        self.assertTrue(Apontamento.objects.filter(minutos=75).exists())
        r = self.client.post("/equipe/", {f"custo_{self.filho.pk}": "61,50", f"custo_{self.pai.pk}": "90", "margem": "35"})
        self.assertEqual(Membro.objects.get(usuario=self.filho).custo_hora, Decimal("61.50"))
        for url in ["/horas/", f"/horas/?usuario={self.pai.pk}", "/rentabilidade/", "/rentabilidade/?de=2026-10&ate=2026-10",
                    f"/rentabilidade/cliente/{self.padaria.pk}/", "/equipe/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_tempo_ao_entregar_obrigacao(self):
        from obrigacoes import services as ob
        from obrigacoes.models import Entrega
        ob.aplicar_padrao_regime(self.padaria)
        ob.gerar_mes(2026, 3)
        e = Entrega.objects.filter(obrigacao__sigla="DAS").first()
        self.client.force_login(self.filho)
        self.client.post(f"/obrigacoes/{e.pk}/entregar/", {"tempo": "0:40"})
        ap = Apontamento.objects.get(entrega=e)
        self.assertEqual((ap.minutos, ap.atividade, ap.cliente), (40, "FISCAL", self.padaria))

    def test_pai_nao_muda_custos(self):
        self.client.force_login(self.pai)
        self.assertEqual(self.client.get("/equipe/").status_code, 403)
