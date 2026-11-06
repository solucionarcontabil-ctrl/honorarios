"""Separa os dois mundos: cliente só enxerga /portal/, equipe não cai no portal por engano."""
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect

from .services import acesso_de

LIBERADOS_PARA_CLIENTE = ("/portal/", "/sair/", "/entrar/", "/static/")


class PortalMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        acesso = acesso_de(request.user)
        request.acesso_portal = acesso
        caminho = request.path
        if acesso:
            if not acesso.ativo:
                logout(request)
                messages.error(request, "Seu acesso ao portal está desativado. Fale com o escritório.")
                return redirect("login")
            if not caminho.startswith(LIBERADOS_PARA_CLIENTE):
                return redirect("portal_inicio")
        elif request.user.is_authenticated and caminho.startswith("/portal/"):
            return redirect("painel")  # equipe do escritório usa o sistema interno
        return self.get_response(request)
