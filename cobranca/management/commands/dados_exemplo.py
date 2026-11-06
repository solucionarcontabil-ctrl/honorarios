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
        # módulo de gestão: obrigações, tarefas e certificados
        from obrigacoes import services as obrig
        from obrigacoes.models import Certificado, Entrega
        for cli in Cliente.objects.all():
            if cli.documento in ("11222333000181", "45723174000110", "60701190000104"):
                cli.tem_funcionarios = True
                cli.save()
            obrig.aplicar_padrao_regime(cli)
        mes_ant = somar_meses(hoje.replace(day=1), -1)
        for e in obrig.gerar_mes(mes_ant.year, mes_ant.month, admin):
            if e.cliente.documento != "11444777000161":
                e.concluir(e.responsavel or admin, protocolo="REC-" + str(e.pk))
        obrig.gerar_mes(hoje.year, hoje.month, admin)
        Entrega.objects.get_or_create(titulo="Pedir extratos bancários de outubro", defaults=dict(
            cliente=Cliente.objects.get(documento="60701190000104"), prazo=hoje + timedelta(days=2), responsavel=pai))
        for doc, dias in (("11222333000181", 12), ("45723174000110", -3), ("60701190000104", 200)):
            cli = Cliente.objects.get(documento=doc)
            Certificado.objects.get_or_create(cliente=cli, documento=doc, defaults=dict(
                titular=cli.nome.upper(), validade_fim=hoje + timedelta(days=dias), onde_esta="Pasta do escritório"))
        # central de XMLs: notas fictícias de exemplo
        from pathlib import Path

        from xmls.services import importar as importar_xml
        pasta = Path(__file__).resolve().parents[3] / "xmls" / "exemplos"
        importar_xml([(p.name, p.read_bytes()) for p in sorted(pasta.glob("*.xml"))], admin)
        # portal, protocolos e rentabilidade
        from django.core.files.base import ContentFile

        from portal import services as portal
        from rentabilidade.models import Apontamento, Membro
        padaria = Cliente.objects.get(documento="11222333000181")
        acesso, _ = portal.criar_acesso("Maria (Padaria)", "padaria", [padaria, Cliente.objects.get(documento="60701190000104")], admin)
        acesso.usuario.set_password("demo-cliente-123")
        acesso.usuario.save()
        pdf = lambda nome: ContentFile(b"%PDF-1.4 exemplo", name=nome)  # noqa: E731
        portal.publicar(padaria, [pdf("DAS-10-2026.pdf")], admin, "GUIA", "DAS 10/2026", mes_ant,
                        hoje.replace(day=20), Decimal("312.40"), "Guia do Simples Nacional.")
        portal.publicar(padaria, [pdf("holerites-10-2026.pdf"), pdf("folha-resumo-10-2026.pdf")], admin, "FOLHA",
                        "Folha 10/2026", mes_ant)
        portal.receber(padaria, [pdf("extrato-itau-outubro.pdf")], acesso.usuario, "EXTRATO", mes_ant, "Extrato da conta principal")
        Membro.objects.update_or_create(usuario=admin, defaults={"custo_hora": Decimal("75")})
        Membro.objects.update_or_create(usuario=pai, defaults={"custo_hora": Decimal("90")})
        horas = [("11222333000181", admin, 240, "FISCAL"), ("11222333000181", pai, 300, "FOLHA"),
                 ("45723174000110", admin, 360, "CONTABIL"), ("45723174000110", pai, 180, "FISCAL"),
                 ("60701190000104", admin, 420, "FISCAL"), ("60701190000104", pai, 360, "FOLHA"),
                 ("11444777000161", pai, 300, "CONTABIL"), ("33000167000101", admin, 45, "FISCAL"),
                 ("52998224725", admin, 60, "IRPF")]
        for doc, u, minutos, ativ in horas:
            Apontamento.objects.create(usuario=u, cliente=Cliente.objects.get(documento=doc), data=mes_ant.replace(day=10),
                                       minutos=minutos, atividade=ativ, descricao="Rotina do mês")
        self.stdout.write(self.style.SUCCESS("Dados de exemplo criados."))
