"""Rotina diária de e-mails (o contêiner 'lembretes' roda às 8h).

    python manage.py rotina_diaria

- lembretes de honorários (vencendo / em atraso)
- resumo diário do portal (para quem escolheu)
- guias vencendo que o cliente ainda não baixou
- nova tentativa dos avisos que deram erro
Rodar duas vezes no mesmo dia não duplica e-mails.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand

from portal import avisos


class Command(BaseCommand):
    help = "Envia os e-mails automáticos do dia."

    def handle(self, *args, **opts):
        try:
            call_command("enviar_lembretes", stdout=self.stdout)
        except Exception as e:  # um problema não pode impedir o resto
            self.stderr.write(f"Lembretes de honorários: {e}")
        for nome, func in (("Resumos do portal", avisos.resumo_diario), ("Lembretes de guia", avisos.lembrar_guias),
                           ("Reenvios", avisos.reenviar_com_erro)):
            try:
                self.stdout.write(f"{nome}: {func()}")
            except Exception as e:
                self.stderr.write(f"{nome}: {e}")
