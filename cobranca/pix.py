"""Geração de PIX copia-e-cola (BR Code estático) sem depender de banco.

Segue o padrão EMV/BR Code do Banco Central. O dinheiro cai direto na conta
dona da chave PIX. A baixa, nesse caso, é manual (não há aviso automático).
"""
import io
import unicodedata
from decimal import Decimal

import qrcode
import qrcode.image.svg


def _campo(id_, valor):
    return f"{id_}{len(valor):02d}{valor}"


def _limpar(texto, limite):
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return texto.upper().strip()[:limite]


def crc16(payload):
    crc = 0xFFFF
    for byte in payload.encode():
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def gerar_payload(chave, nome, cidade, valor=None, txid="***", descricao=""):
    conta = _campo("00", "br.gov.bcb.pix") + _campo("01", chave.strip())
    if descricao:
        conta += _campo("02", _limpar(descricao, 40))
    txid = "".join(c for c in txid if c.isalnum())[:25] or "***"

    payload = (
        _campo("00", "01")
        + _campo("26", conta)
        + _campo("52", "0000")
        + _campo("53", "986")
        + (_campo("54", f"{Decimal(valor):.2f}") if valor else "")
        + _campo("58", "BR")
        + _campo("59", _limpar(nome, 25))
        + _campo("60", _limpar(cidade, 15))
        + _campo("62", _campo("05", txid))
        + "6304"
    )
    return payload + crc16(payload)


def qrcode_svg(texto):
    img = qrcode.make(texto, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()
