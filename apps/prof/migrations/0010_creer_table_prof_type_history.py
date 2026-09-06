"""
Crée `prof_type_history` sur une instance neuve — et répare l'état qui la décrit.

DEUX défauts se cumulaient depuis `0004` :

1. Le modèle est `managed = False`. Django n'émet donc AUCUNE DDL pour lui :
   `sqlmigrate prof 0004` répond « (no-op) ». La table n'existe que parce
   qu'elle a été créée hors-Django sur les bases historiques. Sur une instance
   fraîchement migrée elle est absente — et le signal `post_save` de `Prof`
   écrit dedans dès la création du premier enseignant : `OperationalError:
   no such table: prof_type_history`.

2. Le `CreateModel` de `0004` a OUBLIÉ la clé étrangère `prof`. L'état de
   migration décrit donc sept colonnes là où le modèle et les bases en portent
   huit. Créer la table depuis cet état produirait un `prof_type_history` sans
   `prof_id` — une table neuve et fausse, pire que pas de table du tout.

D'où l'ordre des deux opérations ci-dessous : on rend l'état exact, PUIS on
crée la table à partir de lui.

Ce que cette migration exécute, selon la base :

  * base existante (iss, esp, siga_prive — la table est là, avec ses données) :
    la garde `table_names()` court-circuite, AUCUNE instruction n'est envoyée.
    `AddField` sur un modèle non géré n'émet pas de DDL non plus. Zéro écriture.
  * base neuve : Django génère lui-même le `CREATE TABLE` et le `ADD
    CONSTRAINT`. Rien n'est écrit à la main, donc la table neuve est identique
    à celle des bases historiques — jusqu'aux noms de contraintes, qui sont des
    empreintes calculées sur le schéma.

Retour arrière : `noop`. On ne supprime jamais une table qui porte des données.
"""
import django.db.models.deletion
from django.db import migrations, models


def creer_si_absente(apps, schema_editor):
    """Crée la table UNIQUEMENT si elle manque. Jamais de DROP, jamais d'écrasement."""
    modele = apps.get_model('prof', 'ProfTypeHistory')
    existantes = schema_editor.connection.introspection.table_names()
    if modele._meta.db_table in existantes:
        return
    schema_editor.create_model(modele)


class Migration(migrations.Migration):

    dependencies = [
        ('prof', '0009_alter_prof_telephone'),
    ]

    operations = [
        # État seulement : le modèle est `managed = False`, aucune DDL n'est
        # émise. C'est la colonne que `0004` avait omise.
        migrations.AddField(
            model_name='proftypehistory',
            name='prof',
            field=models.ForeignKey(
                db_column='prof_id',
                on_delete=django.db.models.deletion.CASCADE,
                related_name='type_history',
                to='prof.prof',
            ),
        ),
        migrations.RunPython(creer_si_absente, migrations.RunPython.noop),
    ]
