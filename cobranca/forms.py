from datetime import date

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import Cliente, Cobranca, Configuracao, Contrato


class BootstrapMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if isinstance(campo, forms.DecimalField):
                # aceita "1.234,56" (padrão brasileiro)
                campo.localize = True
                campo.widget = forms.TextInput(attrs={"inputmode": "decimal"})
                campo.widget.is_localized = True
            w = campo.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("class", "form-check-input")
            elif isinstance(w, forms.Select):
                w.attrs.setdefault("class", "form-select")
            else:
                w.attrs.setdefault("class", "form-control")


DATA = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class ClienteForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Cliente
        fields = [
            "nome", "nome_fantasia", "documento", "regime", "responsavel",
            "tem_funcionarios", "inscricao_estadual", "inscricao_municipal",
            "contato", "email", "whatsapp",
            "cep", "endereco", "numero", "bairro", "cidade", "uf",
            "observacoes", "ativo",
        ]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["responsavel"].queryset = get_user_model().objects.filter(is_active=True)
        self.fields["responsavel"].label_from_instance = lambda u: u.get_full_name() or u.username


class ContratoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Contrato
        exclude = ["cliente", "criado_em"]
        widgets = {"data_inicio": DATA, "data_fim": DATA, "ultimo_reajuste": DATA}


class CobrancaAvulsaForm(BootstrapMixin, forms.ModelForm):
    competencia_mes = forms.CharField(
        label="competência", widget=forms.DateInput(attrs={"type": "month"}),
        initial=lambda: timezone.localdate().strftime("%Y-%m"),
    )

    class Meta:
        model = Cobranca
        fields = ["cliente", "descricao", "valor", "vencimento", "observacoes"]
        widgets = {"vencimento": DATA, "observacoes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = Cliente.objects.filter(ativo=True)

    def clean_competencia_mes(self):
        try:
            ano, mes = self.cleaned_data["competencia_mes"].split("-")
            return date(int(ano), int(mes), 1)
        except (ValueError, AttributeError):
            raise forms.ValidationError("Competência inválida.")


class BaixaForm(BootstrapMixin, forms.Form):
    data_pagamento = forms.DateField(label="data do pagamento", widget=DATA, initial=timezone.localdate)
    valor_pago = forms.DecimalField(label="valor pago (R$)", max_digits=12, decimal_places=2)
    forma = forms.ChoiceField(label="forma", choices=Cobranca.FORMAS_RECEBIMENTO)
    observacao = forms.CharField(label="observação", required=False)


class ConfiguracaoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Configuracao
        fields = "__all__"
        widgets = {
            "mensagem_cobranca": forms.Textarea(attrs={"rows": 6}),
            "mensagem_atraso": forms.Textarea(attrs={"rows": 6}),
        }
