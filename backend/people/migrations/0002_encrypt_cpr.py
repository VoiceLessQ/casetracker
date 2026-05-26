from django.db import migrations, models

import people.fields


def encrypt_existing_cpr(apps, schema_editor):
    """Encrypt any plaintext CPR already in the table and populate its blind
    index. Idempotent (safe to re-run): values that are already ciphertext are
    left as-is."""
    from people import crypto

    Person = apps.get_model("people", "Person")
    for person in Person.objects.all().iterator():
        if not person.cpr:
            person.cpr_bidx = None
            person.save(update_fields=["cpr_bidx"])
            continue
        plaintext = crypto.try_decrypt(person.cpr)
        person.cpr = crypto.encrypt(plaintext)
        person.cpr_bidx = crypto.cpr_blind_index(plaintext)
        person.save(update_fields=["cpr", "cpr_bidx"])


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="person",
            name="cpr_bidx",
            field=models.CharField(
                blank=True, db_index=True, editable=False, max_length=64, null=True
            ),
        ),
        # Widen and drop unique/index from cpr so it can hold ciphertext; still a
        # plain CharField at this point so the backfill can read existing values.
        migrations.AlterField(
            model_name="person",
            name="cpr",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.RunPython(encrypt_existing_cpr, migrations.RunPython.noop),
        # Switch to the encrypting field (no column change — behaviour only).
        migrations.AlterField(
            model_name="person",
            name="cpr",
            field=people.fields.EncryptedCharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddConstraint(
            model_name="person",
            constraint=models.UniqueConstraint(
                condition=models.Q(cpr_bidx__isnull=False),
                fields=["cpr_bidx"],
                name="uniq_person_cpr_bidx",
            ),
        ),
    ]
