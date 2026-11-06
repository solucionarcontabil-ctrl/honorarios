import re

from django.core.exceptions import ValidationError


def so_digitos(valor):
    return re.sub(r"\D", "", valor or "")


def cpf_valido(cpf):
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(cpf[n]) * (i + 1 - n) for n in range(i))
        dig = (soma * 10) % 11 % 10
        if dig != int(cpf[i]):
            return False
    return True


def cnpj_valido(cnpj):
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    for pesos, pos in ((pesos1, 12), (pesos2, 13)):
        soma = sum(int(cnpj[i]) * p for i, p in enumerate(pesos))
        resto = soma % 11
        dig = 0 if resto < 2 else 11 - resto
        if dig != int(cnpj[pos]):
            return False
    return True


def validar_documento(valor):
    d = so_digitos(valor)
    if len(d) == 11 and cpf_valido(d):
        return
    if len(d) == 14 and cnpj_valido(d):
        return
    raise ValidationError({"documento": "CPF/CNPJ inválido."})
