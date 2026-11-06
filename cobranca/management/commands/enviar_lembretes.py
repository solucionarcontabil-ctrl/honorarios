"""Envia lembretes de cobrança por e-mail (rodar 1x por dia, ex.: 8h).

    python manage.py enviar_lembretes            # envia
    python manage.py enviar_lembretes --simular  # só mostra
"""
from datetime import timedelta

from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.utils import timezone

from cobranca.models import Cobranca, Configuracao, LembreteEnviado
from cobranca.services import competencia_texto, montar_mensagem


class Command(BaseCommand):
    help = "Envia lembretes de vencimento e de atraso por e-mail."

    def add_arguments(self, parser):
        parser.add_argument("--simular", action="store_true")

    def handle(self, *args, simular=False, **opts):
        config = Configuracao.atual()
        if not config.lembretes_email_ativos and not simular:
            self.stdout.write("Lembretes desativados nas Configurações.")
            return
        hoje = timezone.localdate()
        alvos = [(f"antes-{config.lembrete_dias_antes}", hoje + timedelta(days=config.lembrete_dias_antes))]
        alvos += [(f"apos-{d}", hoje - timedelta(days=d)) for d in config.dias_lembrete_apos()]
        enviados = 0
        for chave, vencimento in alvos:
            qs = Cobranca.objects.filter(status=Cobranca.ABERTA, vencimento=vencimento).exclude(cliente__email="")
            for c in qs.select_related("cliente"):
                if LembreteEnviado.objects.filter(cobranca=c, chave=chave, canal="email").exists():
                    continue
                assunto = f"{'Lembrete' if chave.startswith('antes') else 'Honorário em aberto'} — {competencia_texto(c.competencia)} — {config.nome_escritorio}"
                self.stdout.write(f"[{chave}] {c.cliente} <{c.cliente.email}>")
                if simular:
                    continue
                try:
                    send_mail(assunto, montar_mensagem(c, config), None, [c.cliente.email])
                    LembreteEnviado.objects.create(cobranca=c, chave=chave, canal="email")
                    enviados += 1
                except IntegrityError:
                    pass
                except Exception as e:
                    self.stderr.write(f"  falhou: {e}")
        self.stdout.write(self.style.SUCCESS(f"{enviados} lembrete(s) enviado(s)."))
