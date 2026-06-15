# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
"""Regression test for department scoping: every department-ownable admin must
inherit ScopedAdmin, so a future ModelAdmin can't silently drop scoping and leak
another department's rows (the opt-in-scoping gap from the security audit)."""
from django.contrib import admin
from django.test import SimpleTestCase

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
