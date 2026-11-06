from datetime import date

from django import forms
from django.contrib.auth import get_user_model

from cobranca.forms import DATA, BootstrapMixin
from cobranca.models import Cliente

from .models import CATEGORIAS, AcessoPortal, Protocolo

MES = forms.DateInput(attrs={"type": "month"})


def _mes(valor):
    if not valor:
        return None
    try:
        ano, mes = valor.split("-")
        return date(int(ano), int(mes), 1)
    except (ValueError, AttributeError):
        raise forms.ValidationError("Competência inválida.")


class PublicarForm(BootstrapMixin, forms.Form):
    cliente = forms.ModelChoiceField(queryset=Cliente.objects.filter(ativo=True))
    categoria = forms.ChoiceField(choices=CATEGORIAS)
    titulo = forms.CharField(label="título", required=False, help_text="Vazio = usa o nome do arquivo.")
    competencia = forms.CharField(label="competência", required=False, widget=MES)
    vencimento = forms.DateField(required=False, widget=DATA)
    valor = forms.DecimalField(label="valor (R$)", required=False, max_digits=12, decimal_places=2,
                               help_text="Para guias de um arquivo só.")
    observacao = forms.CharField(label="mensagem ao cliente", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def clean_competencia(self):
        return _mes(self.cleaned_data["competencia"])


class EnviarClienteForm(BootstrapMixin, forms.Form):
    categoria = forms.ChoiceField(choices=[c for c in CATEGORIAS if c[0] not in ("GUIA",)], initial="EXTRATO")
    competencia = forms.CharField(label="competência (mês de referência)", required=False, widget=MES)
    observacao = forms.CharField(label="observação para o escritório", required=False,
                                 widget=forms.Textarea(attrs={"rows": 2}))

    def clean_competencia(self):
        return _mes(self.cleaned_data["competencia"])


class AcessoForm(BootstrapMixin, forms.Form):
    nome = forms.CharField(label="nome da pessoa")
    login = forms.CharField(label="login", help_text="Sugestão: o e-mail ou o CPF da pessoa.")
    email = forms.EmailField(required=False, help_text="Para onde vão os avisos de documentos novos.")
    telefone = forms.CharField(label="WhatsApp", required=False)
    avisos = forms.ChoiceField(label="avisos por e-mail", choices=AcessoPortal.AVISOS, initial="IMEDIATO", required=False)
    clientes = forms.ModelMultipleChoiceField(queryset=Cliente.objects.filter(ativo=True),
                                              help_text="Segure Ctrl para marcar mais de uma empresa.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["clientes"].widget.attrs["size"] = 8

    def clean_login(self):
        login = self.cleaned_data["login"].strip().lower()
        if get_user_model().objects.filter(username__iexact=login).exists():
            raise forms.ValidationError("Já existe um usuário com esse login.")
        return login


class ProtocoloForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Protocolo
        fields = ["cliente", "tipo", "meio", "contato_nome", "responsavel", "observacoes"]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = Cliente.objects.filter(ativo=True)
        self.fields["responsavel"].queryset = get_user_model().objects.filter(is_active=True, acesso_portal__isnull=True)
        self.fields["responsavel"].label_from_instance = lambda u: u.get_full_name() or u.username


class MinhaContaForm(BootstrapMixin, forms.Form):
    email = forms.EmailField(label="e-mail para avisos", required=False)
    avisos = forms.ChoiceField(label="quero receber", choices=AcessoPortal.AVISOS)
