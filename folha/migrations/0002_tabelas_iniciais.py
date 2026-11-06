from datetime import date

from django.db import migrations

from folha.tabelas_iniciais import TABELAS


def carregar(apps, schema_editor):
    T = apps.get_model("folha", "TabelaLegal")
    for t in TABELAS:
        T.objects.get_or_create(tipo=t["tipo"], vigencia_inicio=date.fromisoformat(t["vigencia_inicio"]),
                                defaults={"dados": t["dados"], "fonte": t["fonte"], "conferida": t["conferida"]})


class Migration(migrations.Migration):
    dependencies = [("folha", "0001_initial")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
