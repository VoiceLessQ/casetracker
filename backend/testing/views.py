# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


@login_required
def stop_impersonation(request):
    """Clear impersonation and return to the admin as the real user.
    Login required (any authenticated user — not necessarily admin) so an
    impersonated non-staff worker can still get back, but an anonymous request
    can't poke the endpoint."""
    if request.session.pop("impersonate_id", None):
        messages.info(request, "Stopped impersonating.")
    return redirect("admin:index")
