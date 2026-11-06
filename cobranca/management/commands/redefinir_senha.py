"""Lista os usuários e redefine a senha (ou cria o usuário, se não existir).

    python manage.py redefinir_senha
"""
import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Mostra os usuários e redefine a senha de um deles."

    def handle(self, *args, **opts):
        User = get_user_model()
        usuarios = list(User.objects.filter(acesso_portal__isnull=True).order_by("username"))
        if usuarios:
            self.stdout.write("Usuários do escritório cadastrados:")
            for u in usuarios:
                self.stdout.write(f"  login: {u.username:<15} nome: {u.get_full_name() or '-':<35} "
                                  f"{'(administrador)' if u.is_superuser else ''}{'' if u.is_active else ' [DESATIVADO]'}")
        else:
            self.stdout.write(self.style.WARNING("Nenhum usuário cadastrado ainda."))
        login = input("\nDigite o login que quer redefinir (ou ENTER para criar um novo): ").strip()
        if not login:
            call_command("criar_usuario")
            return
        u = User.objects.filter(username__iexact=login).first()
        if not u:
            self.stdout.write(self.style.WARNING(f"Não existe o login '{login}'. Vamos criar."))
            call_command("criar_usuario")
            return
        while True:
            s1 = getpass.getpass(f"Nova senha para {u.username} (mínimo 10 caracteres; a digitação não aparece): ")
            if s1 != getpass.getpass("Repita a nova senha: "):
                self.stderr.write("As senhas não conferem. Tente de novo.")
                continue
            try:
                validate_password(s1, u)
                break
            except ValidationError as e:
                self.stderr.write(" ".join(e.messages))
        u.set_password(s1)
        u.is_active = True
        u.save()
        self.stdout.write(self.style.SUCCESS(f"Senha de '{u.username}' redefinida. Entre com o login '{u.username}'."))
