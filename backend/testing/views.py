from django.contrib import messages
from django.shortcuts import redirect


def stop_impersonation(request):
    """Clear impersonation and return to the admin as the real user.
    Plain view (no admin access required) so you can always get back even if
    the impersonated worker can't open the admin."""
    if request.session.pop("impersonate_id", None):
        messages.info(request, "Stopped impersonating.")
    return redirect("admin:index")
