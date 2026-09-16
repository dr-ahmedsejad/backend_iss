"""
L'origine d'une séance : nouvelle valeur « recopie », défaut corrigé, et
réétiquetage de l'existant.

DEUX changements, liés.

1. Le DÉFAUT du champ était « grille ». Toute séance créée sans préciser son
   origine — c'est-à-dire toute saisie à la main — héritait donc de l'étiquette
   « dupliquée du patron ». La case « Rétablir le patron » promet que les
   séances ajoutées à la main ne sont jamais écrasées : sur ces séances-là, la
   promesse était fausse, et le premier écrasement aurait détruit du travail.
   Le défaut devient « manuelle ».

2. Une valeur « recopie » s'ajoute, pour les séances posées en recopiant une
   AUTRE semaine. Elle ne pouvait pas hériter de l'origine de sa source : voir
   le commentaire du modèle.

Le réétiquetage ne se fie PAS à l'étiquette — elle est justement ce qu'on
corrige. Le seul critère sûr est structurel : une séance dont le lien vers la
case de patron (`seance_type`) est NUL n'a jamais été dupliquée, donc elle vient
d'une saisie. Mesuré sur la base `iss` le 16/09/2026 : 1 séance sur 31 dans ce
cas, contre 28 étiquetées « grille » dont 27 portent bien leur lien.

Les permutations ne sont pas touchées : leur étiquette est posée explicitement
par l'écran, elle n'a jamais dépendu du défaut.

Retour arrière : le défaut redevient « grille », mais le réétiquetage n'est PAS
défait — on ne remet pas une étiquette dont on a prouvé qu'elle était fausse.
"""
from django.db import migrations, models


def reetiqueter_les_saisies(apps, schema_editor):
    """« grille » sans lien vers un patron = saisie manuelle mal étiquetée."""
    SeanceReelle = apps.get_model('edt', 'SeanceReelle')
    n = (SeanceReelle.objects
         .filter(origine='grille', seance_type__isnull=True)
         .update(origine='manuelle'))
    if n:
        print('  [edt.0002] %d séance(s) réétiquetée(s) « manuelle » : '
              'étiquetées « grille » sans lien vers une case de patron.' % n)


class Migration(migrations.Migration):

    dependencies = [
        ('edt', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='seancereelle',
            name='origine',
            field=models.CharField(
                choices=[
                    ('grille', 'Dupliquée de la grille type'),
                    ('manuelle', 'Ajoutée manuellement'),
                    ('permutation', "Issue d'une permutation"),
                    ('recopie', "Recopiée d'une autre semaine"),
                ],
                default='manuelle',
                max_length=12,
            ),
        ),
        migrations.RunPython(reetiqueter_les_saisies, migrations.RunPython.noop),
    ]
