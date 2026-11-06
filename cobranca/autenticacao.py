from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class LoginSemMaiusculas(ModelBackend):
    """Aceita o login com qualquer combinação de maiúsculas/minúsculas (William = william)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username:
            u = get_user_model().objects.filter(username__iexact=username.strip()).first()
            if u:
                username = u.username
        return super().authenticate(request, username=username, password=password, **kwargs)
