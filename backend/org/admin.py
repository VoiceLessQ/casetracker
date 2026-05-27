# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
from django.contrib import admin

from .models import Department, Membership


class MembershipInline(admin.TabularInline):
    """Assign a user to departments (and their role there) right on the user
    page — the access-provisioning flow: add user, pick departments + role."""
    model = Membership
    fk_name = "user"
    extra = 1
    autocomplete_fields = ("department",)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "active")
    search_fields = ("name", "code")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "department", "role")
    list_filter = ("department", "role")
    autocomplete_fields = ("user", "department")
