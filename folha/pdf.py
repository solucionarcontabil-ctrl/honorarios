"""Holerite (recibo de pagamento) em PDF."""
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from cobranca.models import Configuracao


def _m(v):
    v = Decimal(v or 0)
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if v else ""


def _holerite(c, h, topo, config):
    f, e, cli = h.folha, h.empregado, h.folha.cliente
    larg = 190 * mm
    x0 = 10 * mm
    y = topo
    c.setLineWidth(0.6)
    c.rect(x0, y - 128 * mm, larg, 128 * mm)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x0 + 3 * mm, y - 6 * mm, cli.nome[:70])
    c.setFont("Helvetica", 8)
    c.drawString(x0 + 3 * mm, y - 10 * mm, f"CNPJ/CPF {cli.documento_formatado}")
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(x0 + larg - 3 * mm, y - 6 * mm, "RECIBO DE PAGAMENTO DE SALÁRIO")
    c.setFont("Helvetica", 8)
    c.drawRightString(x0 + larg - 3 * mm, y - 10 * mm, f"{f.get_tipo_display()} — {f.competencia:%m/%Y}")
    c.line(x0, y - 12 * mm, x0 + larg, y - 12 * mm)
    c.drawString(x0 + 3 * mm, y - 16 * mm, f"Matrícula {e.matricula}  ·  {e.nome}")
    c.drawString(x0 + 3 * mm, y - 20 * mm, f"Cargo: {e.cargo or '—'}   CBO: {e.cbo or '—'}   Admissão: {e.admissao:%d/%m/%Y}   CPF: {e.cpf}")
    c.line(x0, y - 22 * mm, x0 + larg, y - 22 * mm)
    cols = [x0 + 3 * mm, x0 + 27 * mm, x0 + 112 * mm, x0 + 150 * mm, x0 + 187 * mm]
    c.setFont("Helvetica-Bold", 8)
    for txt, xx, al in (("Cód.", cols[0], "l"), ("Descrição", cols[1], "l"), ("Referência", x0 + 128 * mm, "r"),
                        ("Vencimentos", x0 + 160 * mm, "r"), ("Descontos", cols[4], "r")):
        (c.drawString if al == "l" else c.drawRightString)(xx, y - 26 * mm, txt)
    c.setFont("Helvetica", 8)
    yy = y - 31 * mm
    for i in h.itens[:18]:
        c.setFont("Helvetica", 7)
        c.drawString(cols[0], yy, i["codigo"][:14])
        c.setFont("Helvetica", 8)
        c.drawString(cols[1], yy, i["descricao"][:55])
        c.drawRightString(x0 + 128 * mm, yy, i.get("referencia", "")[:14])
        if i["tipo"] == "P":
            c.drawRightString(x0 + 160 * mm, yy, _m(i["valor"]))
        else:
            c.drawRightString(cols[4], yy, _m(i["valor"]))
        yy -= 4 * mm
    base = y - 106 * mm
    c.line(x0, base, x0 + larg, base)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x0 + 112 * mm, base - 4 * mm, "Totais")
    c.drawRightString(x0 + 160 * mm, base - 4 * mm, _m(h.proventos))
    c.drawRightString(cols[4], base - 4 * mm, _m(h.descontos))
    c.setFillColor(colors.HexColor("#eef2f7"))
    c.rect(x0 + 112 * mm, base - 11 * mm, 78 * mm, 6 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.drawString(x0 + 114 * mm, base - 9.5 * mm, "LÍQUIDO A RECEBER")
    c.drawRightString(cols[4], base - 9.5 * mm, "R$ " + _m(h.liquido))
    c.setFont("Helvetica", 7)
    c.drawString(x0 + 3 * mm, base - 4 * mm, f"Salário-base: {_m(e.salario)}   Base INSS: {_m(h.base_inss)}   Base FGTS: {_m(h.base_fgts)}")
    c.drawString(x0 + 3 * mm, base - 8 * mm, f"FGTS do mês: {_m(h.fgts)}   Base IRRF: {_m(h.base_irrf)}")
    c.line(x0, base - 13 * mm, x0 + larg, base - 13 * mm)
    c.drawString(x0 + 3 * mm, base - 18 * mm, "Declaro ter recebido a importância líquida discriminada neste recibo.")
    c.line(x0 + 110 * mm, base - 19 * mm, x0 + 185 * mm, base - 19 * mm)
    c.drawString(x0 + 112 * mm, base - 22 * mm, "Assinatura do empregado / data")
    c.setFont("Helvetica", 6)
    c.drawRightString(x0 + larg, y - 131 * mm, f"Processado por {config.nome_escritorio}")


def holerites_pdf(holerites):
    """Dois holerites por página A4."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    config = Configuracao.atual()
    altura = A4[1]
    for n, h in enumerate(holerites):
        if n and n % 2 == 0:
            c.showPage()
        _holerite(c, h, altura - 10 * mm - (n % 2) * 140 * mm, config)
    if not holerites:
        c.drawString(20 * mm, altura - 20 * mm, "Nenhum holerite.")
    c.save()
    return buf.getvalue()
