"""Tabelas e percentuais usados nas apurações.

Simples Nacional: Anexos I a V da LC 123/2006, na redação da LC 155/2016 (vigentes desde 2018).
Lucro Presumido: Lei 9.249/1995 (art. 15 e 20) e Lei 9.430/1996; PIS/COFINS cumulativos (Lei 9.718/1998).

IMPORTANTE: confira periodicamente se houve mudança legal (ex.: alterações recentes nos
percentuais de presunção para empresas de maior faturamento). Os percentuais de presunção
podem ser sobrescritos por cliente no perfil fiscal.
"""
from decimal import Decimal as D

# (limite superior da faixa de RBT12, alíquota nominal %, parcela a deduzir R$)
ANEXOS = {
    "I": [  # Comércio
        (D("180000.00"), D("4.00"), D("0")), (D("360000.00"), D("7.30"), D("5940")),
        (D("720000.00"), D("9.50"), D("13860")), (D("1800000.00"), D("10.70"), D("22500")),
        (D("3600000.00"), D("14.30"), D("87300")), (D("4800000.00"), D("19.00"), D("378000")),
    ],
    "II": [  # Indústria
        (D("180000.00"), D("4.50"), D("0")), (D("360000.00"), D("7.80"), D("5940")),
        (D("720000.00"), D("10.00"), D("13860")), (D("1800000.00"), D("11.20"), D("22500")),
        (D("3600000.00"), D("14.70"), D("85500")), (D("4800000.00"), D("30.00"), D("720000")),
    ],
    "III": [  # Serviços (inclui os sujeitos ao fator R com folha >= 28%)
        (D("180000.00"), D("6.00"), D("0")), (D("360000.00"), D("11.20"), D("9360")),
        (D("720000.00"), D("13.50"), D("17640")), (D("1800000.00"), D("16.00"), D("35640")),
        (D("3600000.00"), D("21.00"), D("125640")), (D("4800000.00"), D("33.00"), D("648000")),
    ],
    "IV": [  # Serviços com CPP fora do DAS (limpeza, vigilância, obras, advocacia...)
        (D("180000.00"), D("4.50"), D("0")), (D("360000.00"), D("9.00"), D("8100")),
        (D("720000.00"), D("10.20"), D("12420")), (D("1800000.00"), D("14.00"), D("39780")),
        (D("3600000.00"), D("22.00"), D("183780")), (D("4800000.00"), D("33.00"), D("828000")),
    ],
    "V": [  # Serviços (fator R < 28% para as atividades sujeitas ao fator R)
        (D("180000.00"), D("15.50"), D("0")), (D("360000.00"), D("18.00"), D("4500")),
        (D("720000.00"), D("19.50"), D("9900")), (D("1800000.00"), D("20.50"), D("17100")),
        (D("3600000.00"), D("23.00"), D("62100")), (D("4800000.00"), D("30.50"), D("540000")),
    ],
}
LIMITE_SIMPLES = D("4800000.00")
SUBLIMITE_ICMS_ISS = D("3600000.00")
FATOR_R_MINIMO = D("0.28")

TIPOS_SIMPLES = [
    ("SN_I", "Simples — Anexo I (comércio)"),
    ("SN_II", "Simples — Anexo II (indústria)"),
    ("SN_III", "Simples — Anexo III (serviços)"),
    ("SN_IV", "Simples — Anexo IV (serviços, CPP fora)"),
    ("SN_V", "Simples — Anexo V (serviços)"),
    ("SN_FATOR_R", "Simples — Anexo III ou V conforme fator R"),
]

# Presunção do Lucro Presumido: (IRPJ %, CSLL %)
ATIVIDADES_PRESUMIDO = {
    "LP_COMERCIO": ("Comércio e indústria", D("8"), D("12")),
    "LP_SERVICOS": ("Prestação de serviços em geral", D("32"), D("32")),
    "LP_SERVICOS_HOSPITALARES": ("Serviços hospitalares e afins (com requisitos legais)", D("8"), D("12")),
    "LP_TRANSPORTE_CARGA": ("Transporte de cargas", D("8"), D("12")),
    "LP_TRANSPORTE_PASSAGEIROS": ("Transporte de passageiros", D("16"), D("12")),
    "LP_COMBUSTIVEIS": ("Revenda de combustíveis", D("1.6"), D("12")),
    "LP_INTERMEDIACAO_IMOVEIS": ("Intermediação, administração e locação de imóveis", D("32"), D("32")),
}
TIPOS_PRESUMIDO = [(k, f"Presumido — {v[0]}") for k, v in ATIVIDADES_PRESUMIDO.items()]
TIPOS_RECEITA = TIPOS_SIMPLES + TIPOS_PRESUMIDO

IRPJ_ALIQUOTA = D("15")
IRPJ_ADICIONAL = D("10")
IRPJ_LIMITE_ADICIONAL_MES = D("20000")   # R$ 60.000 por trimestre
CSLL_ALIQUOTA = D("9")
PIS_CUMULATIVO = D("0.65")
COFINS_CUMULATIVO = D("3.00")

DARF = {"IRPJ": "2089", "CSLL": "2372", "PIS": "8109", "COFINS": "2172"}


def faixa(anexo, rbt12):
    for i, (limite, aliq, pd) in enumerate(ANEXOS[anexo], start=1):
        if rbt12 <= limite:
            return i, aliq, pd
    limite, aliq, pd = ANEXOS[anexo][-1]
    return 6, aliq, pd  # acima do limite: usa a última faixa, mas a empresa está sujeita a exclusão
