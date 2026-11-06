"""Integração com o Asaas (boleto registrado + PIX dinâmico + baixa automática).

Documentação: https://docs.asaas.com
Funciona em sandbox (testes) ou produção, conforme ASAAS_AMBIENTE.
"""
import logging

import requests
from django.conf import settings
from django.utils import timezone

from .models import Cobranca, Configuracao

log = logging.getLogger(__name__)

URLS = {
    "sandbox": "https://api-sandbox.asaas.com/v3",
    "producao": "https://api.asaas.com/v3",
}


class AsaasErro(Exception):
    pass


def configurado():
    return bool(settings.ASAAS_API_KEY)


def _req(metodo, caminho, **kwargs):
    if not configurado():
        raise AsaasErro("Asaas não configurado (ASAAS_API_KEY vazio).")
    url = URLS.get(settings.ASAAS_AMBIENTE, URLS["sandbox"]) + caminho
    headers = {
        "access_token": settings.ASAAS_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "honorarios-escritorio",
    }
    try:
        r = requests.request(metodo, url, headers=headers, timeout=30, **kwargs)
    except requests.RequestException as e:
        raise AsaasErro(f"Falha de conexão com o Asaas: {e}") from e
    if r.status_code >= 400:
        try:
            erros = "; ".join(e.get("description", "") for e in r.json().get("errors", []))
        except ValueError:
            erros = r.text[:300]
        raise AsaasErro(f"Asaas recusou ({r.status_code}): {erros}")
    return r.json() if r.content else {}


def garantir_cliente(cliente):
    if cliente.asaas_id:
        return cliente.asaas_id
    existentes = _req("GET", "/customers", params={"cpfCnpj": cliente.documento}).get("data", [])
    if existentes:
        cliente.asaas_id = existentes[0]["id"]
    else:
        dados = {
            "name": cliente.nome,
            "cpfCnpj": cliente.documento,
            "email": cliente.email or None,
            "mobilePhone": "".join(ch for ch in cliente.whatsapp if ch.isdigit()) or None,
            "postalCode": "".join(ch for ch in cliente.cep if ch.isdigit()) or None,
            "address": cliente.endereco or None,
            "addressNumber": cliente.numero or None,
            "province": cliente.bairro or None,
            "externalReference": str(cliente.pk),
            "notificationDisabled": True,  # quem avisa o cliente é o escritório
        }
        cliente.asaas_id = _req("POST", "/customers", json={k: v for k, v in dados.items() if v})["id"]
    cliente.save(update_fields=["asaas_id"])
    return cliente.asaas_id


def emitir(cobranca):
    """Cria a cobrança no Asaas e guarda links de boleto/fatura e PIX."""
    if cobranca.gateway_id:
        return cobranca
    if cobranca.status != Cobranca.ABERTA:
        raise AsaasErro("Só é possível emitir cobranças em aberto.")
    config = Configuracao.atual()
    forma = cobranca.contrato.forma_pagamento if cobranca.contrato else "UNDEFINED"
    if forma == "MANUAL":
        forma = "UNDEFINED"
    vencimento = max(cobranca.vencimento, timezone.localdate())
    dados = {
        "customer": garantir_cliente(cobranca.cliente),
        "billingType": forma,
        "value": float(cobranca.valor),
        "dueDate": vencimento.isoformat(),
        "description": cobranca.descricao[:500],
        "externalReference": str(cobranca.pk),
        "fine": {"value": float(config.multa_percentual)},
        "interest": {"value": float(config.juros_mensal_percentual)},
    }
    resp = _req("POST", "/payments", json=dados)
    cobranca.gateway_id = resp["id"]
    cobranca.link_fatura = resp.get("invoiceUrl") or ""
    cobranca.link_boleto = resp.get("bankSlipUrl") or ""
    if forma in ("PIX", "UNDEFINED"):
        try:
            cobranca.pix_copia_cola = _req("GET", f"/payments/{resp['id']}/pixQrCode").get("payload", "")
        except AsaasErro as e:
            log.warning("PIX não gerado para %s: %s", cobranca.pk, e)
    cobranca.save()
    return cobranca


def cancelar(cobranca):
    if cobranca.gateway_id:
        _req("DELETE", f"/payments/{cobranca.gateway_id}")


EVENTOS_PAGO = {"PAYMENT_RECEIVED", "PAYMENT_CONFIRMED", "PAYMENT_RECEIVED_IN_CASH"}
FORMA_POR_TIPO = {"BOLETO": "BOLETO", "PIX": "PIX", "CREDIT_CARD": "CARTAO", "DEBIT_CARD": "CARTAO"}


def processar_webhook(evento):
    """Recebe o aviso do Asaas e dá baixa automática. Retorna texto do que foi feito."""
    tipo = evento.get("event", "")
    pagamento = evento.get("payment") or {}
    gid = pagamento.get("id")
    if not gid:
        return "ignorado: sem pagamento"
    cobranca = Cobranca.objects.filter(gateway_id=gid).first()
    if not cobranca:
        return f"ignorado: cobrança {gid} desconhecida"

    if tipo in EVENTOS_PAGO and cobranca.status == Cobranca.ABERTA:
        data = pagamento.get("clientPaymentDate") or pagamento.get("paymentDate") or timezone.localdate().isoformat()
        cobranca.registrar_pagamento(
            data_pagamento=data,
            valor_pago=pagamento.get("value") or cobranca.valor,
            forma=FORMA_POR_TIPO.get(pagamento.get("billingType"), "OUTRO"),
        )
        return f"baixada cobrança {cobranca.pk}"
    if tipo == "PAYMENT_DELETED" and cobranca.status == Cobranca.ABERTA:
        cobranca.status = Cobranca.CANCELADA
        cobranca.save(update_fields=["status", "atualizado_em"])
        return f"cancelada cobrança {cobranca.pk}"
    if tipo in ("PAYMENT_REFUNDED", "PAYMENT_CHARGEBACK_REQUESTED") and cobranca.status == Cobranca.PAGA:
        cobranca.status = Cobranca.ABERTA
        cobranca.data_pagamento = None
        cobranca.valor_pago = None
        cobranca.observacoes += f"\n[{timezone.localdate():%d/%m/%Y}] Estorno informado pelo Asaas ({tipo})."
        cobranca.save()
        return f"reaberta cobrança {cobranca.pk}"
    return f"sem ação para {tipo}"
