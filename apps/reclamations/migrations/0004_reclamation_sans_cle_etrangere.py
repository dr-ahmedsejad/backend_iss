"""
La réclamation d'un étudiant devient sans clé étrangère — SANS perdre une ligne.

Pourquoi : sur le miroir, elle appartient à la boîte de réception, exclue
TOTALEMENT de la publication. Avec ses cinq contraintes vers des tables
publiées, la restauration échouait (pg_dump --clean ne droppe jamais en
cascade). Voir le modèle `Reclamation`.

Écrite à la main, dans l'ordre qui ne perd rien :
  1. ajouter les colonnes de l'instantané ;
  2. les REMPLIR depuis les relations, tant qu'elles existent encore ;
  3. passer chaque clé étrangère à db_constraint=False : la contrainte part,
     la colonne et son index restent ;
  4. dans l'ÉTAT des migrations seulement, remplacer la relation par un entier
     portant le MÊME nom de colonne (etudiant → etudiant_id), en bigint comme
     les clés primaires — rien n'est exécuté en base.

Réversible : le retour arrière repose les contraintes (et échoue, à juste
titre, si une ligne vise une cible disparue).
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def remplir_instantane(apps, schema_editor):
    Reclamation = apps.get_model('reclamations', 'Reclamation')
    qs = Reclamation.objects.select_related(
        'etudiant', 'traitee_par', 'inscription_element__em', 'presence__suivi__em')
    for r in qs.iterator():
        em = None
        if r.inscription_element_id and r.inscription_element.em_id:
            em = r.inscription_element.em
        elif r.presence_id and r.presence.suivi_id and r.presence.suivi.em_id:
            em = r.presence.suivi.em
        r.etudiant_nom = r.etudiant.nom or ''
        r.etudiant_matricule = r.etudiant.matricule or ''
        r.em_id = em.pk if em else None
        r.em_code = (em.code_em or '') if em else ''
        r.em_intitule = (em.intitule or '') if em else ''
        r.traitee_par_nom = ((r.traitee_par.name or r.traitee_par.username or '')
                             if r.traitee_par_id else '')
        r.save(update_fields=['etudiant_nom', 'etudiant_matricule', 'em_id',
                              'em_code', 'em_intitule', 'traitee_par_nom'])


class Migration(migrations.Migration):

    dependencies = [
        ('reclamations', '0003_alter_reclamation_justificatif'),
        ('absence', '0006_alter_etudiant_departement'),
        ('inscriptions', '0017_candidatbac'),
        ('evaluations', '0016_sessionevaluation_rattrapage_vci_actif'),
        ('em', '0013_em_filiere_stable'),
        ('suivi', '0015_creer_table_pointage_departements'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── 1. L'instantané ───────────────────────────────────────────────────
        migrations.AddField('reclamation', 'etudiant_nom',
                            models.CharField(blank=True, default='', max_length=200)),
        migrations.AddField('reclamation', 'etudiant_matricule',
                            models.CharField(blank=True, default='', max_length=50)),
        migrations.AddField('reclamation', 'em_id',
                            models.BigIntegerField(blank=True, db_index=True, null=True)),
        migrations.AddField('reclamation', 'em_code',
                            models.CharField(blank=True, default='', max_length=50)),
        migrations.AddField('reclamation', 'em_intitule',
                            models.CharField(blank=True, default='', max_length=200)),
        migrations.AddField('reclamation', 'traitee_par_nom',
                            models.CharField(blank=True, default='', max_length=150)),

        # ── 2. Le remplir, tant que les relations existent ────────────────────
        migrations.RunPython(remplir_instantane, migrations.RunPython.noop),

        # ── 3. Retirer les contraintes — colonnes et index restent ────────────
        migrations.AlterField('reclamation', 'etudiant', models.ForeignKey(
            'absence.etudiant', on_delete=django.db.models.deletion.CASCADE,
            db_constraint=False, related_name='reclamations')),
        migrations.AlterField('reclamation', 'presence', models.ForeignKey(
            'absence.presence', on_delete=django.db.models.deletion.SET_NULL,
            db_constraint=False, null=True, blank=True, related_name='reclamations')),
        migrations.AlterField('reclamation', 'inscription_element', models.ForeignKey(
            'inscriptions.inscriptionelement', on_delete=django.db.models.deletion.SET_NULL,
            db_constraint=False, null=True, blank=True, related_name='reclamations')),
        migrations.AlterField('reclamation', 'session_evaluation', models.ForeignKey(
            'evaluations.sessionevaluation', on_delete=django.db.models.deletion.SET_NULL,
            db_constraint=False, null=True, blank=True, related_name='reclamations')),
        migrations.AlterField('reclamation', 'traitee_par', models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL,
            db_constraint=False, null=True, blank=True, related_name='reclamations_traitees')),

        # ── 4. Des entiers, dans l'ÉTAT seulement — même colonne, même type ───
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField('reclamation', 'etudiant'),
                migrations.RemoveField('reclamation', 'presence'),
                migrations.RemoveField('reclamation', 'inscription_element'),
                migrations.RemoveField('reclamation', 'session_evaluation'),
                migrations.RemoveField('reclamation', 'traitee_par'),
                migrations.AddField('reclamation', 'etudiant_id',
                                    models.BigIntegerField(db_index=True, default=0),
                                    preserve_default=False),
                migrations.AddField('reclamation', 'presence_id',
                                    models.BigIntegerField(blank=True, db_index=True, null=True)),
                migrations.AddField('reclamation', 'inscription_element_id',
                                    models.BigIntegerField(blank=True, db_index=True, null=True)),
                migrations.AddField('reclamation', 'session_evaluation_id',
                                    models.BigIntegerField(blank=True, db_index=True, null=True)),
                migrations.AddField('reclamation', 'traitee_par_id',
                                    models.BigIntegerField(blank=True, db_index=True, null=True)),
            ],
        ),
    ]
