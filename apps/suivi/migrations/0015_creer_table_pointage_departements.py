"""
Crée `suivi_pointage_departements` sur une instance neuve.

C'est la table de liaison qui porte la fusion multi-départements du pointage —
donc, en bout de chaîne, la paie. Le through-model est `managed = False` et
`0008` est un `SeparateDatabaseAndState` avec `database_operations=[]` : Django
n'émet aucune DDL, `sqlmigrate suivi 0008` répond « (no-op) ». La table n'existe
que sur les bases historiques, où elle a été créée hors-Django. Sur une instance
fraîchement migrée elle est absente.

Un écart d'état, plus discret que celui de `prof_type_history` mais réel :
`0008` déclare `id` en `AutoField` (integer) alors que le modèle et les trois
bases portent un `bigint`. On aligne l'état avant de créer, sinon une instance
neuve naîtrait déjà divergente.

Ce que cette migration exécute, selon la base :

  * base existante : la garde `table_names()` court-circuite, aucune
    instruction. `AlterField` sur un modèle non géré n'émet pas de DDL.
    Les 2 938 lignes de l'ISS, 300 de l'ESP et 60 de SIGA-PRIVE ne sont pas
    touchées.
  * base neuve : Django génère le `CREATE TABLE`, la contrainte d'unicité
    `(suiviepointage_id, departement_id)` et les deux clés étrangères.

Retour arrière : `noop`.
"""
from django.db import migrations, models


def creer_si_absente(apps, schema_editor):
    """Crée la table UNIQUEMENT si elle manque. Jamais de DROP, jamais d'écrasement."""
    modele = apps.get_model('suivi', 'SuiviePointageDepartement')
    existantes = schema_editor.connection.introspection.table_names()
    if modele._meta.db_table in existantes:
        return
    schema_editor.create_model(modele)


class Migration(migrations.Migration):

    dependencies = [
        ('suivi', '0014_fix_duree_creneau'),
        # Les deux clés étrangères visent `suivi_suivie_pointage` et
        # `departement` : ces tables doivent exister avant la création.
        ('departement', '0001_initial'),
    ]

    operations = [
        # État seulement — modèle non géré, aucune DDL.
        migrations.AlterField(
            model_name='suiviepointagedepartement',
            name='id',
            field=models.BigAutoField(
                auto_created=True, primary_key=True,
                serialize=False, verbose_name='ID',
            ),
        ),
        migrations.RunPython(creer_si_absente, migrations.RunPython.noop),
    ]
