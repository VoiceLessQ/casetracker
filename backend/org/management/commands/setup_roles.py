from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

# Default-access baseline: the model-level permissions a caseworker needs so
# the admin shows the right things. Per-department refinement (viewer vs
# member vs lead) is enforced separately by Membership.role in the admin.
#
# Editable models get view/add/change (NOT delete — deletes are superuser-only,
# and the append-only logs forbid edits/deletes in admin regardless).
EDIT_MODELS = [
    ("cases", "case"),
    ("cases", "statusevent"),
    ("cases", "document"),
    ("cases", "followup"),
    ("cases", "caselog"),
    ("cases", "caseassignment"),
    ("cases", "calendarevent"),
    ("cases", "caselegalref"),
    ("people", "person"),
    ("people", "relationship"),
    ("people", "personnote"),
]
# Catalogs and access tables a caseworker reads but does not manage.
VIEW_MODELS = [
    ("cases", "casecategory"),
    ("cases", "circumstance"),
    ("cases", "regulationrule"),
    ("cases", "legalreference"),
    ("org", "department"),
    ("org", "membership"),
]

CASEWORKER_GROUP = "Caseworker"
INTAKE_GROUP = "Borgerservice (intake)"


class Command(BaseCommand):
    help = (
        "Create/refresh the 'Caseworker' permission group — the default set of "
        "capabilities IT assigns to staff. Department-level access is then set "
        "per user via their membership role (viewer / member / lead). "
        "Idempotent."
    )

    def _perms(self, specs, actions):
        codenames, cts = [], []
        for app_label, model in specs:
            ct = ContentType.objects.get(app_label=app_label, model=model)
            cts.append(ct.id)
            codenames += [f"{a}_{model}" for a in actions]
        return Permission.objects.filter(content_type_id__in=cts, codename__in=codenames)

    def handle(self, *args, **options):
        group, _ = Group.objects.get_or_create(name=CASEWORKER_GROUP)
        perms = list(self._perms(EDIT_MODELS, ("view", "add", "change")))
        perms += list(self._perms(VIEW_MODELS, ("view",)))
        group.permissions.set(perms)

        # Borgerservice / intake: the routine broad-search role. Caseworkers are
        # scoped to their departments' people; intake can find/register anyone
        # (logged hard, but without the break-the-glass friction). This is RBAC
        # for the routine — break-the-glass stays reserved for the exception.
        intake, _ = Group.objects.get_or_create(name=INTAKE_GROUP)
        intake.permissions.set(
            Permission.objects.filter(
                content_type=ContentType.objects.get(app_label="people", model="person"),
                codename="search_all_persons",
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"'{CASEWORKER_GROUP}' group ready with {len(perms)} permissions.\n"
                f"'{INTAKE_GROUP}' group ready (broad citizen search for intake).\n"
                "Provision a caseworker: tick 'Staff status', add the "
                "'Caseworker' group, then set their department role(s) in the "
                "membership inline on the user page. Front-desk/borgerservice also "
                f"get the '{INTAKE_GROUP}' group."
            )
        )
