from .models import Configuracao


def escritorio(request):
    if not request.user.is_authenticated:
        return {}
    if getattr(request, "acesso_portal", None):
        return {"escritorio": Configuracao.atual()}
    from rentabilidade.models import Apontamento
    return {
        "escritorio": Configuracao.atual(),
        "cronometro": Apontamento.objects.filter(usuario=request.user, em_andamento=True).select_related("cliente").first(),
    }
