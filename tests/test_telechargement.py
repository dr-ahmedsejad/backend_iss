"""
Le nom d'un fichier téléchargé doit survivre à ses accents.

Mesuré le 01/10/2026 sur le VPS : l'emploi du temps de « Statistiques, Economie
et Applications — G1 » partait avec

    Content-Disposition: =?utf-8?b?YXR0YWNobWVudDsgZmlsZW5hbWU9…?=

Le seul tiret long du titre suffisait : Django, voyant un caractère non ASCII,
encode l'en-tête ENTIER au format des courriels — que les navigateurs ne
décodent pas. Le fichier n'était plus annoncé comme une pièce jointe et son nom
était perdu.

Ce qui tient ici : l'en-tête reste de l'ASCII pur, donc Django n'y touche plus.
"""
import pytest

from core.telechargement import entete_piece_jointe


class TestEnTete:

    def test_un_nom_accentue_ne_fait_plus_encoder_l_entete(self):
        """Le cas de production, celui qui a tout déclenché."""
        v = entete_piece_jointe('emploi_Statistiques,_Economie_et_Applications_—_G1_S1.pdf')
        # L'en-tête est ASCII : Django ne le réencodera pas.
        v.encode('ascii')
        assert not v.startswith('=?')
        # Le nom EXACT voyage dans `filename*`, le tiret long pourcent-encodé.
        assert "filename*=UTF-8''" in v
        assert '%E2%80%94' in v

    def test_le_nom_de_secours_est_lisible_sans_accents(self):
        v = entete_piece_jointe('Relevé_de_notes_Élève.pdf')
        assert 'filename="Releve_de_notes_Eleve.pdf"' in v
        assert "filename*=UTF-8''Relev%C3%A9_de_notes_%C3%89l%C3%A8ve.pdf" in v

    def test_un_nom_deja_ascii_reste_intact(self):
        v = entete_piece_jointe('maquette_SEA.pdf')
        assert v == ('attachment; filename="maquette_SEA.pdf"; '
                     "filename*=UTF-8''maquette_SEA.pdf")

    def test_inline_pour_un_apercu(self):
        assert entete_piece_jointe('doc.pdf', inline=True).startswith('inline; ')


class TestCaracteresDangereux:

    @pytest.mark.parametrize('nom', [
        'rapport"; attachment; filename="pirate.exe',   # ferme le paramètre
        'rapport;charset=evil.pdf',                     # ajoute une directive
        'rapport\r\nX-Injecte: oui.pdf',                # injecte un en-tête
    ])
    def test_un_nom_ne_peut_pas_fabriquer_un_autre_entete(self, nom):
        v = entete_piece_jointe(nom)

        # Un en-tête HTTP tient sur UNE ligne : un saut en fabriquerait un second.
        assert '\r' not in v and '\n' not in v
        # La structure reste celle qu'on a écrite, et une seule fois.
        assert v.count('attachment;') == 1
        assert v.count('; filename="') == 1
        assert v.count("; filename*=UTF-8''") == 1
        # Le nom de secours est entre guillemets : ni guillemet ni point-virgule
        # ne doit s'y trouver, sinon il ferme le paramètre et la suite se lit
        # comme une directive.
        secours = v.split('; filename="', 1)[1].split('"', 1)[0]
        assert '"' not in secours and ';' not in secours

    def test_un_nom_vide_recoit_un_nom(self):
        assert entete_piece_jointe('') == ('attachment; filename="document"; '
                                           "filename*=UTF-8''document")

    def test_un_nom_sans_aucune_lettre_latine_garde_un_secours(self):
        """« جدول » ne laisse RIEN en ASCII. Sans garde, le secours serait vide :
        `filename=""` est un en-tête invalide, et certains navigateurs
        enregistrent alors le fichier sous le nom de l'URL."""
        v = entete_piece_jointe('جدول')
        assert 'filename="document"' in v
        assert "filename*=UTF-8''%D8%AC%D8%AF%D9%88%D9%84" in v
        v.encode('ascii')

    def test_une_extension_seule_suffit_comme_secours(self):
        """« جدول.pdf » garde au moins son extension : inutile de tout remplacer."""
        assert 'filename=".pdf"' in entete_piece_jointe('جدول.pdf')
