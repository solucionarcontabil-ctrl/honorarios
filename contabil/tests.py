from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from cobranca.models import Cliente

from . import plano_padrao, rotinas, services
from .models import ItemExtrato, Lancamento, LogContabil, RegraExtrato
from .services import ErroContabil, PartidaDados as P

D = Decimal

OFX = b"""OFXHEADER:100
DATA:OFXSGML
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260105120000[-3:BRT]<TRNAMT>1500.00<FITID>A1<MEMO>PIX RECEBIDO CLIENTE X
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260106<TRNAMT>-35,90<FITID>A2<MEMO>TARIFA PACOTE SERVICOS
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260107<TRNAMT>-800.00<FITID>A3<NAME>ALUGUEL JANEIRO
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""


class Base(TestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_superuser("contador", "c@c.com", "senha-muito-forte-1")
        self.c = Cliente.objects.create(nome="Padaria", documento="11222333000181", regime="SIMPLES")
        plano_padrao.aplicar(self.c)
        self.conta = lambda cod: self.c.contas.get(codigo=cod)
        self.caixa, self.banco = self.conta("1.1.01.001"), self.conta("1.1.01.002")
        self.capital, self.vendas = self.conta("2.3.01.001"), self.conta("3.1.01.001")
        self.aluguel, self.cmv = self.conta("4.2.02.001"), self.conta("4.1.01.001")

    def lanc(self, data, deb, cred, valor, hist="teste"):
        return services.lancar(self.c, data, hist, [P(deb, "D", D(valor)), P(cred, "C", D(valor))], self.u)


class PlanoTest(Base):
    def test_plano_padrao(self):
        self.assertEqual(self.c.contas.count(), len(plano_padrao.PLANO))
        self.assertTrue(self.conta("1.1.01").sintetica)
        self.assertFalse(self.caixa.sintetica)
        self.assertEqual(self.conta("2.3.01.001").natureza, "03")
        self.assertTrue(self.aluguel.devedora)
        self.assertFalse(self.vendas.devedora)
        with self.assertRaises(ValueError):
            plano_padrao.aplicar(self.c)


class LancamentoTest(Base):
    def test_validacoes(self):
        with self.assertRaises(ErroContabil):  # débito diferente de crédito
            services.lancar(self.c, date(2026, 1, 2), "x", [P(self.caixa, "D", D("10")), P(self.capital, "C", D("9"))], self.u)
        with self.assertRaises(ErroContabil):  # conta sintética
            self.lanc(date(2026, 1, 2), self.conta("1.1.01"), self.capital, "10")
        outro = Cliente.objects.create(nome="Outro", documento="45723174000110")
        plano_padrao.aplicar(outro)
        with self.assertRaises(ErroContabil):  # conta de outra empresa
            self.lanc(date(2026, 1, 2), outro.contas.get(codigo="1.1.01.001"), self.capital, "10")
        with self.assertRaises(ErroContabil):  # valor zero
            self.lanc(date(2026, 1, 2), self.caixa, self.capital, "0")
        self.assertEqual(Lancamento.objects.count(), 0)

    def test_numeracao_e_auditoria(self):
        a = self.lanc(date(2026, 1, 2), self.caixa, self.capital, "10000")
        b = self.lanc(date(2026, 1, 3), self.banco, self.caixa, "5000")
        self.assertEqual((a.numero, b.numero), (1, 2))
        self.assertEqual(LogContabil.objects.filter(acao="incluir").count(), 2)

    def test_periodo_fechado_e_estorno(self):
        a = self.lanc(date(2026, 1, 10), self.aluguel, self.caixa, "800")
        services.fechar_periodo(self.c, date(2026, 1, 31), self.u)
        with self.assertRaises(ErroContabil):
            self.lanc(date(2026, 1, 15), self.aluguel, self.caixa, "1")
        with self.assertRaises(ErroContabil):
            services.excluir(a, self.u)
        with self.assertRaises(ErroContabil):
            services.alterar(a, date(2026, 2, 1), "x", [P(self.aluguel, "D", D(1)), P(self.caixa, "C", D(1))], self.u)
        e = services.estornar(a, date(2026, 2, 1), self.u, "conta errada")
        self.assertEqual({(p.conta, p.tipo) for p in e.partidas.all()}, {(self.aluguel, "C"), (self.caixa, "D")})
        with self.assertRaises(ErroContabil):
            services.estornar(a, date(2026, 2, 2), self.u)
        with self.assertRaises(ErroContabil):  # não reabre para trás sem ser pela reabertura
            services.fechar_periodo(self.c, date(2025, 12, 31), self.u)
        services.reabrir_periodo(self.c, None, self.u, "retificação")
        self.assertIsNone(services.fechado_ate(self.c))
        self.assertTrue(LogContabil.objects.filter(acao="reabrir_periodo").exists())

    def test_alterar_registra_antes_e_depois(self):
        a = self.lanc(date(2026, 1, 10), self.aluguel, self.caixa, "800")
        services.alterar(a, date(2026, 1, 11), "aluguel jan", [P(self.aluguel, "D", D("850")), P(self.banco, "C", D("850"))], self.u)
        log = LogContabil.objects.get(acao="alterar")
        self.assertEqual(log.antes["partidas"][0]["valor"], "800.00")
        self.assertEqual(log.depois["partidas"][0]["valor"], "850.00")


class RelatoriosTest(Base):
    def setUp(self):
        super().setUp()
        self.lanc(date(2025, 12, 1), self.caixa, self.capital, "10000", "integralização")
        self.lanc(date(2026, 1, 5), self.caixa, self.vendas, "3000", "vendas")
        self.lanc(date(2026, 1, 6), self.cmv, self.caixa, "1200", "custo")
        self.lanc(date(2026, 1, 7), self.aluguel, self.caixa, "800", "aluguel")

    def test_balancete(self):
        linhas, td, tc = services.balancete(self.c, date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(td, tc)
        por = {l["conta"].codigo: l for l in linhas}
        self.assertEqual(por["1.1.01.001"]["saldo_ant"], D("10000"))
        self.assertEqual(por["1.1.01.001"]["saldo"], D("11000"))
        self.assertEqual(por["1"]["saldo"], D("11000"))   # sintética soma as filhas
        self.assertEqual(por["3.1.01.001"]["saldo"], D("-3000"))  # credor

    def test_dre_e_balanco_fecham(self):
        d = services.dre(self.c, date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual((d["lucro_bruto"], d["liquido"]), (D("1800"), D("1000")))
        b = services.balanco(self.c, date(2026, 1, 31))
        self.assertEqual(b["ativo"], D("11000"))
        self.assertEqual(b["diferenca"], D("0"))

    def test_razao(self):
        inicial, linhas, final = services.razao(self.caixa, date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual((inicial, final, len(linhas)), (D("10000"), D("11000"), 3))

    def test_encerramento(self):
        services.encerrar_exercicio(self.c, 2026, self.u)
        linhas, _, _ = services.balancete(self.c, date(2026, 1, 1), date(2026, 12, 31))
        por = {l["conta"].codigo: l["saldo"] for l in linhas}
        self.assertEqual(por.get("3.1.01.001", D(0)), D(0))
        self.assertEqual(por.get("4.2.02.001", D(0)), D(0))
        self.assertEqual(por["2.3.03.001"], D("-1000"))  # lucro acumulado (credor)
        self.assertEqual(por.get("2.3.03.003", D(0)), D(0))  # ARE zerada
        self.assertEqual(services.dre(self.c, date(2026, 1, 1), date(2026, 12, 31))["liquido"], D("1000"))
        self.assertEqual(services.balanco(self.c, date(2026, 12, 31))["diferenca"], D(0))
        with self.assertRaises(ErroContabil):
            services.encerrar_exercicio(self.c, 2026, self.u)


class ExtratoTest(Base):
    def test_ofx_regra_e_contabilizacao(self):
        RegraExtrato.objects.create(cliente=self.c, texto="tarifa", sentido="SAIDA", conta=self.conta("4.3.01.001"))
        novos, rep = rotinas.importar_ofx(self.c, self.banco, OFX)
        self.assertEqual((novos, rep), (3, 0))
        self.assertEqual(rotinas.importar_ofx(self.c, self.banco, OFX), (0, 3))
        tarifa = ItemExtrato.objects.get(fitid="A2")
        self.assertEqual((tarifa.valor, tarifa.contrapartida.codigo), (D("-35.90"), "4.3.01.001"))
        feitos, erros = rotinas.contabilizar_itens(self.c, list(ItemExtrato.objects.all()), self.u)
        self.assertEqual(feitos, 1)  # só o que tem contrapartida
        l = Lancamento.objects.get(origem="OFX")
        self.assertEqual({(p.conta.codigo, p.tipo) for p in l.partidas.all()}, {("4.3.01.001", "D"), ("1.1.01.002", "C")})

    def test_ofx_invalido(self):
        with self.assertRaises(rotinas.OFXInvalido):
            rotinas.ler_ofx(b"nada aqui")


class NotasTest(Base):
    def test_cfops(self):
        for cfop, entrada, esperado in [("5102", False, True), ("6108", False, True), ("5405", False, True),
                                        ("5202", False, False), ("5910", False, False), ("5152", False, False),
                                        ("5949", False, False), ("5556", False, False), ("1102", True, True),
                                        ("5102", True, False), ("5409", False, False), ("5656", False, True)]:
            self.assertEqual(rotinas.cfop_operacional(cfop, entrada), esperado, cfop)

    def test_contabilizar_notas_dos_xmls(self):
        from xmls.services import importar
        ex = Path(__file__).resolve().parents[1] / "xmls" / "exemplos"
        importar([(p.name, p.read_bytes()) for p in sorted(ex.glob("*.xml"))])
        itens = rotinas.previa_notas(self.c, date(2026, 10, 1))
        self.assertEqual({(i["doc"].numero, i["tipo"], i["valor"]) for i in itens},
                         {("1234", "SAIDA", D("1000.00")), ("555", "ENTRADA", D("2500.00"))})  # 1235 cancelada fora
        feitos, erros = rotinas.contabilizar_notas(self.c, date(2026, 10, 1), self.u)
        self.assertEqual((feitos, erros), (2, []))
        self.assertEqual(rotinas.contabilizar_notas(self.c, date(2026, 10, 1), self.u), (0, []))  # não duplica
        linhas, td, tc = services.balancete(self.c, date(2026, 10, 1), date(2026, 10, 31))
        por = {l["conta"].codigo: l["saldo"] for l in linhas}
        self.assertEqual((por["1.1.02.001"], por["1.1.04.001"], por["2.1.01.001"]), (D(1000), D(2500), D(-2500)))


class TelasTest(Base):
    def test_telas_e_fluxo(self):
        self.client.force_login(self.u)
        cid = self.c.pk
        r = self.client.post(f"/contabil/{cid}/lancamentos/novo/", {
            "data": "2026-01-10", "historico": "Aluguel de janeiro", "documento": "REC-1",
            "p_conta": [f"{self.aluguel.reduzido} - x", self.caixa.codigo, ""], "p_tipo": ["D", "C", "D"],
            "p_valor": ["1.234,56", "1234,56", ""], "p_complemento": ["", "", ""]})
        self.assertEqual(r.status_code, 302)
        l = Lancamento.objects.get()
        self.assertEqual(l.valor, D("1234.56"))
        r = self.client.post(f"/contabil/{cid}/lancamentos/novo/", {
            "data": "2026-01-10", "historico": "errado", "p_conta": [self.aluguel.codigo, self.caixa.codigo],
            "p_tipo": ["D", "C"], "p_valor": ["10", "9"], "p_complemento": ["", ""]})
        self.assertContains(r, "diferentes dos créditos")
        arq = SimpleUploadedFile("extrato.ofx", OFX)
        self.client.post(f"/contabil/{cid}/extrato/", {"acao": "importar", "conta_banco": self.banco.pk, "arquivo": arq})
        self.assertEqual(ItemExtrato.objects.count(), 3)
        it = ItemExtrato.objects.get(fitid="A3")
        self.client.post(f"/contabil/{cid}/extrato/", {"acao": "contabilizar", "ids": [it.pk],
                                                       f"cp_{it.pk}": self.aluguel.codigo, f"regra_{it.pk}": "ALUGUEL"})
        it.refresh_from_db()
        self.assertEqual(it.status, "CONTABILIZADO")
        self.assertTrue(RegraExtrato.objects.filter(texto="ALUGUEL").exists())
        for url in ["/contabil/", f"/contabil/{cid}/", f"/contabil/{cid}/plano/", f"/contabil/{cid}/plano/nova/",
                    f"/contabil/{cid}/plano/{self.caixa.pk}/", f"/contabil/{cid}/lancamentos/{l.pk}/",
                    f"/contabil/{cid}/extrato/", f"/contabil/{cid}/extrato/?status=TODOS", f"/contabil/{cid}/notas/",
                    f"/contabil/{cid}/balancete/?de=2026-01-01&ate=2026-12-31", f"/contabil/{cid}/razao/?conta={self.caixa.reduzido}",
                    f"/contabil/{cid}/diario/", f"/contabil/{cid}/dre/", f"/contabil/{cid}/balanco/",
                    f"/contabil/{cid}/fechamento/", f"/contabil/{cid}/auditoria/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        x = self.client.get(f"/contabil/{cid}/balancete/?de=2026-01-01&ate=2026-12-31&excel=1")
        self.assertIn("spreadsheetml", x["Content-Type"])
        self.client.post(f"/contabil/{cid}/fechamento/", {"acao": "fechar", "ate": "2026-01-31"})
        r = self.client.get(f"/contabil/{cid}/lancamentos/{l.pk}/")
        self.assertContains(r, "só pode ser estornado")
