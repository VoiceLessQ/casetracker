# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
from django.conf import settings
from django.db import models


class Department(models.Model):
    """A municipal department. This is the scoping unit: a user only sees
    cases owned by departments they belong to."""

    code = models.SlugField(max_length=32, unique=True)   # e.g. "social", "finance"
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Membership(models.Model):
    """Which user belongs to which department, and at what level.
    A user may belong to several departments."""

    class Role(models.TextChoices):
        VIEWER = "viewer", "Viewer"      # read-only
        MEMBER = "member", "Member"      # normal caseworker
        LEAD = "lead", "Lead"            # can reassign / hand off

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="memberships",
    )
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="memberships",
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)

    class Meta:
        unique_together = ("user", "department")

    def __str__(self):
        return f"{self.user} @ {self.department} ({self.role})"
