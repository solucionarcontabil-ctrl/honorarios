from cobranca.equipe import equipe
from django import forms
from django.contrib.auth import get_user_model

from cobranca.forms import DATA, BootstrapMixin
from cobranca.models import Cliente

from .models import Certificado, Entrega, Feriado, Obrigacao


def _usuarios(campo):
    campo.queryset = equipe()
    campo.label_from_instance = lambda u: u.get_full_name() or u.username


class ObrigacaoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Obrigacao
        fields = "__all__"
        widgets = {"observacao": forms.Textarea(attrs={"rows": 2})}


class TarefaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Entrega
        fields = ["titulo", "cliente", "prazo", "responsavel", "observacoes"]
        widgets = {"prazo": DATA, "observacoes": forms.Textarea(attrs={"rows": 3})}
        labels = {"titulo": "o que precisa ser feito"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = Cliente.objects.filter(ativo=True)
        self.fields["cliente"].required = False
        _usuarios(self.fields["responsavel"])


class EntregaEditarForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Entrega
        fields = ["titulo", "prazo", "responsavel", "status", "protocolo", "observacoes"]
        widgets = {"prazo": DATA, "observacoes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _usuarios(self.fields["responsavel"])


class CertificadoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Certificado
        exclude = ["criado_em"]
        widgets = {"validade_inicio": DATA, "validade_fim": DATA, "observacoes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = Cliente.objects.filter(ativo=True)


class LerPfxForm(BootstrapMixin, forms.Form):
    arquivo = forms.FileField(label="arquivo do certificado A1 (.pfx ou .p12)")
    senha = forms.CharField(label="senha do certificado", widget=forms.PasswordInput, required=False)
    onde_esta = forms.CharField(label="onde está guardado", required=False)

    def clean_arquivo(self):
        f = self.cleaned_data["arquivo"]
        if f.size > 50_000:
            raise forms.ValidationError("Arquivo grande demais para um certificado A1.")
        return f


class FeriadoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Feriado
        fields = ["data", "descricao"]
        widgets = {"data": DATA}


class ImportarClientesForm(BootstrapMixin, forms.Form):
    arquivo = forms.FileField(label="planilha (.xlsx ou .csv)")
    responsavel = forms.ModelChoiceField(queryset=None, required=False, label="responsável pelos novos clientes")
    aplicar_obrigacoes = forms.BooleanField(
        label="já vincular as obrigações padrão do regime", required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _usuarios(self.fields["responsavel"])

    def clean_arquivo(self):
        f = self.cleaned_data["arquivo"]
        if not f.name.lower().endswith((".xlsx", ".xlsm", ".csv", ".txt")):
            raise forms.ValidationError("Envie um arquivo .xlsx ou .csv. (Arquivos .xls antigos: abra no Excel e salve como .xlsx.)")
        if f.size > 10 * 1024 * 1024:
            raise forms.ValidationError("Planilha maior que 10 MB.")
        return f
