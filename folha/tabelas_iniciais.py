"""Tabelas legais iniciais. As marcadas conferida=False precisam ser conferidas com a norma oficial
(em Folha > Tabelas legais) antes de fechar folhas que as usem."""

TABELAS = [
    # ---------------- 2025 (valores conhecidos)
    {"tipo": "SALARIO_MINIMO", "vigencia_inicio": "2025-01-01", "fonte": "Decreto nº 12.342/2024", "conferida": True,
     "dados": {"valor": "1518.00"}},
    {"tipo": "INSS", "vigencia_inicio": "2025-01-01", "fonte": "Portaria Interministerial MPS/MF nº 6/2025", "conferida": True,
     "dados": {"faixas": [["1518.00", "7.5"], ["2793.88", "9"], ["4190.83", "12"], ["8157.41", "14"]], "teto": "8157.41"}},
    {"tipo": "IRRF", "vigencia_inicio": "2024-02-01", "fonte": "Lei nº 14.848/2024", "conferida": True,
     "dados": {"faixas": [["2259.20", "0", "0"], ["2826.65", "7.5", "169.44"], ["3751.05", "15", "381.44"],
                          ["4664.68", "22.5", "662.77"], [None, "27.5", "896.00"]],
               "dependente": "189.59", "simplificado": "564.80", "redutor": None}},
    {"tipo": "IRRF", "vigencia_inicio": "2025-05-01", "fonte": "MP nº 1.294/2025 / Lei nº 15.191/2025", "conferida": True,
     "dados": {"faixas": [["2428.80", "0", "0"], ["2826.65", "7.5", "182.16"], ["3751.05", "15", "394.16"],
                          ["4664.68", "22.5", "675.49"], [None, "27.5", "908.73"]],
               "dependente": "189.59", "simplificado": "607.20", "redutor": None}},
    {"tipo": "SALARIO_FAMILIA", "vigencia_inicio": "2025-01-01", "fonte": "Portaria Interministerial MPS/MF nº 6/2025",
     "conferida": True, "dados": {"limite": "1906.04", "cota": "65.00"}},
    # ---------------- 2026 (A CONFERIR com a norma oficial)
    {"tipo": "SALARIO_MINIMO", "vigencia_inicio": "2026-01-01", "fonte": "A CONFERIR — decreto do salário mínimo de 2026",
     "conferida": False, "dados": {"valor": "1621.00"}},
    {"tipo": "INSS", "vigencia_inicio": "2026-01-01", "fonte": "A CONFERIR — portaria interministerial MPS/MF de 2026",
     "conferida": False,
     "dados": {"faixas": [["1621.00", "7.5"], ["2902.84", "9"], ["4354.27", "12"], ["8475.55", "14"]], "teto": "8475.55"}},
    {"tipo": "IRRF", "vigencia_inicio": "2026-01-01", "fonte": "A CONFERIR — Lei nº 15.270/2025 (redução até R$ 5.000)",
     "conferida": False,
     "dados": {"faixas": [["2428.80", "0", "0"], ["2826.65", "7.5", "182.16"], ["3751.05", "15", "394.16"],
                          ["4664.68", "22.5", "675.49"], [None, "27.5", "908.73"]],
               "dependente": "189.59", "simplificado": "607.20",
               "redutor": {"isencao_ate": "5000.00", "reducao_maxima": "312.89", "faixa_ate": "7350.00",
                           "constante": "978.62", "fator": "0.133145"}}},
]
