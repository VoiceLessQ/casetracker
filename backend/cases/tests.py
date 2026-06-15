# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
"""Regression test for department scoping: every department-ownable admin must
inherit ScopedAdmin, so a future ModelAdmin can't silently drop scoping and leak
another department's rows (the opt-in-scoping gap from the security audit)."""
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, SimpleTestCase, TestCase

from cases.admin import ScopedAdmin
from cases.models import (
    Case,
    CaseAssignment,
    CaseHandoff,
    CaseLog,
    Document,
    FollowUp,
    StatusEvent,
)
from org.models import Department, Membership

User = get_user_model()


class ScopedAdminRegressionTests(SimpleTestCase):
    # Models whose rows belong to a department and must never be visible across
    # department boundaries. Adding a new such model? Register its admin as a
    # ScopedAdmin subclass and add it here.
    SCOPED_MODELS = [Case, StatusEvent, Document, FollowUp, CaseLog, CaseAssignment, CaseHandoff]

    def test_sensitive_case_admins_are_department_scoped(self):
        for model in self.SCOPED_MODELS:
            with self.subTest(model=model.__name__):
                model_admin = admin.site._registry.get(model)
                self.assertIsNotNone(model_admin, f"{model.__name__} is not registered in admin")
                self.assertIsInstance(
                    model_admin, ScopedAdmin,
                    f"{model.__name__} admin must inherit ScopedAdmin (department scoping)",
                )


class DepartmentIsolationTests(TestCase):
    """A user in one department must not reach another department's case — not by
    listing, and not by putting a foreign PK in the URL (the IDOR vector)."""

    def setUp(self):
        self.factory = RequestFactory()
        self.dept_a = Department.objects.create(code="a", name="Dept A")
        self.dept_b = Department.objects.create(code="b", name="Dept B")
        case_perms = list(Permission.objects.filter(
            content_type__app_label="cases", codename__in=("view_case", "change_case"),
        ))
        self.user_a = User.objects.create_user("worker_a", is_staff=True)
        self.user_a.user_permissions.add(*case_perms)
        Membership.objects.create(user=self.user_a, department=self.dept_a)
        self.user_b = User.objects.create_user("worker_b", is_staff=True)
        self.user_b.user_permissions.add(*case_perms)
        Membership.objects.create(user=self.user_b, department=self.dept_b)

        self.case_a = Case.objects.create(
            ref="A-1", title="Dept A case", owner_department=self.dept_a,
            created_by=self.user_a,
        )
        self.case_admin = admin.site._registry[Case]

    def _request(self, user):
        request = self.factory.get("/admin/cases/case/")
        request.user = user
        return request

    def test_queryset_excludes_other_departments(self):
        a_ids = set(self.case_admin.get_queryset(self._request(self.user_a)).values_list("id", flat=True))
        b_ids = set(self.case_admin.get_queryset(self._request(self.user_b)).values_list("id", flat=True))
        self.assertIn(self.case_a.id, a_ids)
        self.assertNotIn(self.case_a.id, b_ids)

    def test_get_object_blocks_foreign_department_pk(self):
        # The admin change / delete / history views fetch the row through
        # get_object() -> get_queryset(), so a foreign-department PK resolves to
        # nothing. This is the exact mechanism that stops URL/PK tampering.
        self.assertEqual(
            self.case_admin.get_object(self._request(self.user_a), str(self.case_a.pk)),
            self.case_a,
        )
        self.assertIsNone(
            self.case_admin.get_object(self._request(self.user_b), str(self.case_a.pk)),
        )

    def test_change_url_does_not_expose_foreign_case(self):
        url = f"/admin/cases/case/{self.case_a.pk}/change/"
        # Own department reaches the change page.
        self.client.force_login(self.user_a)
        self.assertEqual(self.client.get(url).status_code, 200)
        # Other department is bounced (404 or redirect away) — never a 200 render.
        self.client.force_login(self.user_b)
        self.assertNotEqual(self.client.get(url).status_code, 200)
