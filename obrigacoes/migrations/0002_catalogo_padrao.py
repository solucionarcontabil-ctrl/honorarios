from django.db import migrations

from obrigacoes.services import AVISO_PRAZOS, CATALOGO_PADRAO


def carregar(apps, schema_editor):
    Obrigacao = apps.get_model("obrigacoes", "Obrigacao")
    for sig, nome, orgao, per, regra, dia, meses, mes_anual, ajuste, regimes, func in CATALOGO_PADRAO:
        Obrigacao.objects.get_or_create(sigla=sig, defaults=dict(
            nome=nome, orgao=orgao, periodicidade=per, regra_prazo=regra, dia=dia or 1, meses_apos=meses,
            mes_anual=mes_anual, ajuste=ajuste, regimes=regimes, so_com_funcionarios=func, observacao=AVISO_PRAZOS,
        ))


class Migration(migrations.Migration):
    dependencies = [("obrigacoes", "0001_initial")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
