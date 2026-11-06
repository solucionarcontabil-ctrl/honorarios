"""Cria um usuário da equipe com nome completo (usado para ligar o 'Contador' do Domínio ao responsável).

    python manage.py criar_usuario                       # pergunta tudo
    python manage.py criar_usuario --nome "Fulano de Tal" --login fulano --email f@x.com --admin
"""
import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Cria um usuário da equipe do escritório."

    def add_arguments(self, parser):
        parser.add_argument("--nome")
        parser.add_argument("--login")
        parser.add_argument("--email", default="")
        parser.add_argument("--admin", action="store_true", help="Pode mexer em configurações e tabelas.")
        parser.add_argument("--senha", help="Só para testes automáticos; prefira digitar quando pedir.")

    def handle(self, *args, **o):
        User = get_user_model()
        nome = o["nome"] or input("Nome completo (igual ao campo 'Contador' do Domínio): ").strip()
        login = (o["login"] or input("Login (ex.: william): ").strip()).lower()
        email = o["email"] if o["nome"] else input("E-mail (opcional): ").strip()
        admin = o["admin"] if o["nome"] else input("Administrador? (s/n): ").strip().lower().startswith("s")
        if not nome or not login:
            raise CommandError("Nome e login são obrigatórios.")
        if User.objects.filter(username__iexact=login).exists():
            raise CommandError(f"Já existe o login '{login}'.")
        senha = o["senha"]
        while not senha:
            s1 = getpass.getpass("Senha (mínimo 10 caracteres): ")
            if s1 != getpass.getpass("Repita a senha: "):
                self.stderr.write("As senhas não conferem.")
                continue
            try:
                validate_password(s1)
                senha = s1
            except ValidationError as e:
                self.stderr.write(" ".join(e.messages))
        partes = nome.split()
        User.objects.create_user(login, email, senha, first_name=partes[0], last_name=" ".join(partes[1:]),
                                 is_staff=admin, is_superuser=admin)
        self.stdout.write(self.style.SUCCESS(f"Usuário '{login}' ({nome}) criado."))
