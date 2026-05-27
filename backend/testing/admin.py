# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.shortcuts import redirect

from org.admin import MembershipInline

User = get_user_model()


@admin.action(description="Impersonate for testing (view as this user)")
def impersonate(modeladmin, request, queryset):
    # request.user here is the real superuser (not yet swapped on this POST).
    if not request.user.is_superuser:
        modeladmin.message_user(request, "Only superusers can impersonate.", messages.ERROR)
        return
    if queryset.count() != 1:
        modeladmin.message_user(request, "Select exactly one user.", messages.ERROR)
        return
    target = queryset.first()
    if not target.is_staff:
        modeladmin.message_user(
            request,
            f"{target} is not staff, so they can't open the admin — you'll see "
            f"nothing. Set is_staff on them to test, then visit /stop-impersonation/.",
            messages.WARNING,
        )
    request.session["impersonate_id"] = target.pk
    modeladmin.message_user(
        request,
        f"Now viewing as {target}. Go to /stop-impersonation/ to return.",
        messages.INFO,
    )
    return redirect("admin:index")


admin.site.unregister(User)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    actions = [impersonate]
    inlines = [MembershipInline]
