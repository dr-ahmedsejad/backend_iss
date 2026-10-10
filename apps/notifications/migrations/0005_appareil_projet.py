# App d'où vient le jeton (« gp » = Groupe Polytechnique). AJOUT d'une colonne
# vide : les inscriptions existantes (anciennes apps) gardent la valeur ''.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0004_index_consultations'),
    ]

    operations = [
        migrations.AddField(
            model_name='appareilpush',
            name='projet',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
    ]
