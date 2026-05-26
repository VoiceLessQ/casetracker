from django.db import migrations, models

import people.fields


def encrypt_existing(apps, schema_editor):
    """Encrypt any plaintext note / address / note-text already stored.
    Idempotent: values that already decrypt are left unchanged."""
    from people import crypto

    Person = apps.get_model("people", "Person")
    for p in Person.objects.all().iterator():
        changed = []
        if p.note:
            p.note = crypto.encrypt(crypto.try_decrypt(p.note))
            changed.append("note")
        if p.address:
            p.address = crypto.encrypt(crypto.try_decrypt(p.address))
            changed.append("address")
        if changed:
            p.save(update_fields=changed)

    PersonNote = apps.get_model("people", "PersonNote")
    for n in PersonNote.objects.all().iterator():
        if n.text:
            n.text = crypto.encrypt(crypto.try_decrypt(n.text))
            n.save(update_fields=["text"])


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0002_encrypt_cpr"),
    ]

    operations = [
        # Widen address so it can hold ciphertext; still plain for the backfill.
        migrations.AlterField(
            model_name="person",
            name="address",
            field=models.CharField(blank=True, max_length=600),
        ),
        migrations.RunPython(encrypt_existing, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="person",
            name="note",
            field=people.fields.EncryptedTextField(blank=True),
        ),
        migrations.AlterField(
            model_name="person",
            name="address",
            field=people.fields.EncryptedCharField(blank=True, max_length=600),
        ),
        migrations.AlterField(
            model_name="personnote",
            name="text",
            field=people.fields.EncryptedTextField(),
        ),
    ]
