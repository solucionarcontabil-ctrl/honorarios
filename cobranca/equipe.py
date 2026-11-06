from django.contrib.auth import get_user_model


def equipe():
    """Usuários do escritório (exclui os logins de clientes do portal)."""
    return get_user_model().objects.filter(is_active=True, acesso_portal__isnull=True).order_by("first_name", "username")
