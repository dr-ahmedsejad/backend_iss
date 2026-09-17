"""
Les fériés à date fixe de Mauritanie.

Par une migration, et non à la main dans la base : un déploiement neuf doit les
avoir aussi.

LISTE À CONFIRMER : elle vient d'un autre dépôt et n'a pas été vérifiée contre
le texte officiel.

Les fêtes religieuses — Aïd el-Fitr, Aïd el-Adha, Nouvel An de l'Hégire,
Mawlid — n'y sont PAS : elles suivent le calendrier lunaire et changent de date
chaque année. On les marque à la main, jour par jour.

Cette migration ne touche PAS au calendrier : elle remplit la table des fériés
fixes, rien d'autre. Les appliquer aux semaines existantes est une décision, qui
passe par l'action « appliquer au calendrier ».
"""
from django.db import migrations

FERIES = [
    (1, 1, "Jour de l'An"),
    (1, 5, 'Fête du Travail'),
    (25, 5, "Journée de l'Afrique"),
    (28, 11, "Fête de l'Indépendance"),
]


def inserer(apps, schema_editor):
    JourFerieFixe = apps.get_model('parametres', 'JourFerieFixe')
    for jour, mois, libelle in FERIES:
        # Sur (jour, mois) seulement : rejouer ne crée pas de doublon et
        # n'écrase pas un libellé modifié depuis.
        JourFerieFixe.objects.get_or_create(
            jour=jour, mois=mois, defaults={'libelle': libelle, 'actif': True})


def retirer(apps, schema_editor):
    JourFerieFixe = apps.get_model('parametres', 'JourFerieFixe')
    for jour, mois, _ in FERIES:
        JourFerieFixe.objects.filter(jour=jour, mois=mois).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('parametres', '0016_jour_ferie_fixe'),
    ]

    operations = [
        migrations.RunPython(inserer, retirer),
    ]
