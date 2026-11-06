"""Cria clientes e cobranças fictícios para testar o sistema (NÃO use em produção)."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from cobranca.models import Cliente, Cobranca, Configuracao, Contrato
from cobranca.services import gerar_mensalidades, somar_meses


class Command(BaseCommand):
    help = "Popula o banco com dados fictícios para demonstração."

    def handle(self, *args, **opts):
        User = get_user_model()
        admin = User.objects.filter(is_superuser=True).first()
        pai, _ = User.objects.get_or_create(username="litoral", defaults={"first_name": "Escritório Litoral"})
        cfg = Configuracao.atual()
        cfg.nome_escritorio = "Contabilidade Exemplo"
        cfg.chave_pix = "12345678000195"
        cfg.nome_recebedor_pix = "CONTABILIDADE EXEMPLO"
        cfg.cidade = "SAO PAULO"
        cfg.save()
        # CNPJs válidos fictícios
        dados = [
            ("Padaria Pão Quente Ltda", "Pão Quente", "11222333000181", "SIMPLES", 650, 10, admin),
            ("Auto Peças Litoral ME", "Auto Peças Litoral", "11444777000161", "SIMPLES", 890, 5, pai),
            ("Clínica Sorriso S/S", "Clínica Sorriso", "45723174000110", "PRESUMIDO", 1800, 10, admin),
            ("João da Silva", "", "52998224725", "PF", 250, 15, pai),
            ("Mercadinho Bom Preço Eireli", "Bom Preço", "60701190000104", "SIMPLES", 1200, 10, admin),
            ("Studio Design MEI", "", "33000167000101", "MEI", 180, 20, pai),
        ]
        inicio = somar_meses(timezone.localdate().replace(day=1), -14)
        for nome, fantasia, doc, regime, valor, dia, resp in dados:
            cli, _ = Cliente.objects.get_or_create(documento=doc, defaults=dict(
                nome=nome, nome_fantasia=fantasia, regime=regime, responsavel=resp,
                email="cliente@exemplo.com", whatsapp="11999998888", contato=nome.split()[0]))
            Contrato.objects.get_or_create(cliente=cli, defaults=dict(
                valor_mensal=Decimal(valor), dia_vencimento=dia, data_inicio=inicio,
                mes_reajuste=timezone.localdate().month))
        hoje = timezone.localdate()
        for i in range(5, 0, -1):
            comp = somar_meses(hoje.replace(day=1), -i)
            for c in gerar_mensalidades(comp, admin):
                atrasa = c.cliente.documento in ("11444777000161", "33000167000101") and i <= 3
                if c.vencimento < hoje and not atrasa:
                    c.registrar_pagamento(c.vencimento - timedelta(days=1), c.valor, "PIX", admin)
        self.stdout.write(self.style.SUCCESS("Dados de exemplo criados."))
