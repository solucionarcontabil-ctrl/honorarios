from .models import Configuracao


def escritorio(request):
    if not request.user.is_authenticated:
        return {}
    return {"escritorio": Configuracao.atual()}
