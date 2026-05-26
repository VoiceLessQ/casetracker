"""Seed a small, synthetic demo designed to SHOW the access model, not just
fill the dashboard. After running, log in (or impersonate) as different workers
and watch the same system show different things — and watch the shielded person
disappear for a worker who has no grant.

PLACEHOLDER DATA ONLY — every CPR/name here is fake (test-range CPRs).
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from org.models import Department, Membership
from people.models import Person
from cases.models import Case, CaseAssignment, FollowUp, CaseCategory

DEMO_PASSWORD = "demo12345"   # synthetic demo only


class Command(BaseCommand):
    help = "Seed synthetic departments, workers, people, and cases to demo the access model. Idempotent."

    def handle(self, *args, **options):
        User = get_user_model()
        call_command("setup_roles")  # ensure Caseworker + Borgerservice groups exist
        caseworker = Group.objects.get(name="Caseworker")
        intake = Group.objects.get(name="Borgerservice (intake)")

        # Departments
        social = Department.objects.get_or_create(code="social", defaults={"name": "Socialforvaltning"})[0]
        teknik = Department.objects.get_or_create(code="teknik", defaults={"name": "Teknik & Miljø"})[0]
        borger = Department.objects.get_or_create(code="borger", defaults={"name": "Borgerservice"})[0]

        def worker(username, full, dept, role, extra_group=None):
            u, _ = User.objects.get_or_create(username=username, defaults={"first_name": full})
            u.first_name = full
            u.is_staff = True
            u.is_superuser = False
            u.set_password(DEMO_PASSWORD)
            u.save()
            u.groups.add(caseworker)
            if extra_group:
                u.groups.add(extra_group)
            Membership.objects.update_or_create(user=u, department=dept, defaults={"role": role})
            return u

        anna = worker("anna", "Anna (social member)", social, Membership.Role.MEMBER)
        bo = worker("bo", "Bo (social lead)", social, Membership.Role.LEAD)
        david = worker("david", "David (teknik member)", teknik, Membership.Role.MEMBER)
        clara = worker("clara", "Clara (borgerservice intake)", borger, Membership.Role.MEMBER, intake)

        # People (synthetic). One is shielded.
        def person(name, cpr, address, shielded=False):
            p, _ = Person.objects.get_or_create(name=name, defaults={})
            p.cpr = cpr
            p.address = address
            p.is_shielded = shielded
            p.save()
            return p

        jens = person("Jens Demosen", "010101-0001", "Testvej 1, Nuuk")
        marie = person("Marie Demoki", "020202-0002", "Prøvegade 2, Nuuk")
        peter = person("Peter Demosen", "030303-0003", "Demovej 3, Nuuk")
        shielded = person("Sara Skjult", "040404-0004", "HEMMELIG", shielded=True)

        cat = CaseCategory.objects.get_or_create(code="borgersag", defaults={"name": "Borgersag"})[0]
        cat.departments.add(social)

        def case(ref, title, dept, person_obj, status=Case.Status.IN_PROGRESS, assignee=None):
            c, _ = Case.objects.get_or_create(
                ref=ref,
                defaults={"title": title, "owner_department": dept, "created_by": bo,
                          "person": person_obj, "category": cat, "status": status},
            )
            if assignee:
                CaseAssignment.objects.get_or_create(
                    case=c, worker=assignee, defaults={"role": CaseAssignment.Role.LEAD, "active": True}
                )
            return c

        # Social: assigned to Anna (her "my cases"), unassigned (queue), shielded, and a stale one.
        case("SOC-101", "Boligstøtte", social, jens, assignee=anna)
        case("SOC-102", "Familieydelse", social, marie, assignee=anna)
        case("SOC-103", "Ny henvendelse", social, peter, status=Case.Status.NEW)            # queue (unassigned)
        case("SOC-104", "Beskyttet sag", social, shielded, assignee=bo)                      # shielded person
        stale = case("SOC-105", "Afventer svar (gået i stå)", social, jens)                  # will be made stale
        # Teknik: David's case — Anna must NOT see this.
        case("TEK-201", "Byggetilladelse", teknik, peter, assignee=david)

        # Make SOC-105 unexplained-stale: old activity, not muted, not WAITING, no review window.
        Case.objects.filter(ref="SOC-105").update(updated_at=timezone.now() - timedelta(days=90))

        # Follow-ups for Anna (one overdue, one upcoming).
        FollowUp.objects.get_or_create(
            case=Case.objects.get(ref="SOC-101"), what="Ring til borger",
            defaults={"assignee": anna, "due_date": date.today() - timedelta(days=2)},
        )
        FollowUp.objects.get_or_create(
            case=Case.objects.get(ref="SOC-102"), what="Indhent dokumentation",
            defaults={"assignee": anna, "due_date": date.today() + timedelta(days=3)},
        )

        self.stdout.write(self.style.SUCCESS(
            "Seeded demo data.\n"
            f"  Login password for all demo workers: {DEMO_PASSWORD}\n"
            "  Workers: anna (social/member), bo (social/lead), david (teknik/member), "
            "clara (borgerservice/intake).\n"
            "  Open /dashboard/ as each — Anna sees only Social cases, not TEK-201; "
            "SOC-105 shows under 'Needs attention'.\n"
            "  Search /admin/people/person/?q=Sara as anna: the shielded person does NOT "
            "appear; as a superuser it does. That contrast is the demo."
        ))
