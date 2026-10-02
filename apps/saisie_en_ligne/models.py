"""
Les notes saisies EN LIGNE par un enseignant — un BROUILLON, jamais une note.

Sur le serveur de travail, l'enseignant saisit directement les notes
officielles (`evaluations_note`). Sur le miroir, c'est impossible : la table
des notes y est publiée, donc réécrite — et une note officielle ne s'écrit pas
depuis un serveur exposé à Internet. La saisie en ligne va donc ici : le
personnel l'exporte en tableur et la RESSAISIT sur le serveur de travail.
Rien n'écrit d'ici vers les notes officielles, et aucun test ne doit le
permettre (tests/test_miroir_boite.py).

Boîte de réception : exclusion TOTALE de la publication, AUCUNE clé
étrangère — identifiants bruts et instantané lisible figé à la saisie. Gardé
par tests/test_miroir_invariant.py.
"""
from django.db import models


class SaisieNoteEnLigne(models.Model):
    # ── Ce qui est noté — identifiants bruts ───────────────────────────────
    session_id             = models.BigIntegerField(db_index=True)
    inscription_element_id = models.BigIntegerField(db_index=True)
    em_id                  = models.BigIntegerField(null=True, blank=True, db_index=True)
    etudiant_id            = models.BigIntegerField(null=True, blank=True)

    # ── Instantané lisible ─────────────────────────────────────────────────
    session_libelle    = models.CharField(max_length=200, blank=True, default='')
    em_code            = models.CharField(max_length=50, blank=True, default='')
    em_intitule        = models.CharField(max_length=200, blank=True, default='')
    etudiant_matricule = models.CharField(max_length=50, blank=True, default='')
    etudiant_nom       = models.CharField(max_length=200, blank=True, default='')

    # ── Les notes du brouillon (vide = pas de note) ────────────────────────
    cc   = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    tp   = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    exam = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    saisi_par_id  = models.BigIntegerField(null=True, blank=True)
    saisi_par_nom = models.CharField(max_length=150, blank=True, default='')
    cree_le       = models.DateTimeField(auto_now_add=True)
    modifie_le    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'saisie_note_en_ligne'
        ordering = ['em_code', 'etudiant_matricule']
        constraints = [
            models.UniqueConstraint(fields=['session_id', 'inscription_element_id'],
                                    name='saisie_en_ligne_unique_par_inscription'),
        ]

    def __str__(self):
        return f'brouillon {self.em_code} {self.etudiant_matricule} (session {self.session_id})'
