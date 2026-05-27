# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
from django.contrib.auth import get_user_model


class ImpersonationMiddleware:
    """Lets a superuser 'view as' another user, so they can test exactly what a
    caseworker / social worker sees and can do — scoping, append-only rules,
    permissions all key off request.user, so swapping it tests everything.

    Rules:
      - Only the REAL session user being a superuser can trigger a swap.
      - The real user is preserved as request.impersonator.
      - Session key `impersonate_id` drives it; cleared via /stop-impersonation/.

    This is a TESTING tool. In a real deployment, impersonation is a high-trust,
    heavily audited capability — every swap and every action taken while
    impersonating must be logged, and it should be restricted far more tightly
    than 'any superuser'.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        real = getattr(request, "user", None)
        target_id = request.session.get("impersonate_id")
        if target_id and real is not None and real.is_authenticated and real.is_superuser:
            User = get_user_model()
            try:
                target = User.objects.get(pk=target_id)
                request.impersonator = real
                request.user = target
            except User.DoesNotExist:
                request.session.pop("impersonate_id", None)
        return self.get_response(request)
