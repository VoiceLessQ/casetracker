from django.core.management.base import BaseCommand

from cases.models import CaseCategory

# Starter catalog of Danish municipal case/folder types. Created GLOBAL
# (no department) so an admin can assign each to the departments that use it.
# This is a starting set, not a lock-in: rename, deactivate, or reassign freely.
CATEGORIES = [
    ("emnesag", "Emnesag"),
    ("borgersag", "Borgersag"),
    ("personalesag", "Personalesag"),
    ("politisk-sag", "Politisk sag"),
    ("aktindsigtssag", "Aktindsigtssag"),
    ("byggesag", "Byggesag"),
    ("ejendomssag", "Ejendomssag"),
    ("projektrum", "Projekt / samarbejdsrum"),
    ("elevmappe", "Elevmappe"),
    ("ppr-sag", "PPR-sag"),
    ("tilsynssag", "Tilsynssag"),
    ("ministerbetjening", "Ministerbetjening"),
]


class Command(BaseCommand):
    help = (
        "Seed a starter catalog of case categories (Danish municipal case "
        "types). Idempotent: existing codes are left untouched. Categories are "
        "created global; assign them to departments in the admin."
    )

    def handle(self, *args, **options):
        created, skipped = 0, 0
        for code, name in CATEGORIES:
            _, was_created = CaseCategory.objects.get_or_create(
                code=code, defaults={"name": name}
            )
            if was_created:
                created += 1
                self.stdout.write(f"  + {code}")
            else:
                skipped += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded case categories: {created} created, {skipped} already existed."
            )
        )
