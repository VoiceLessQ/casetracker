# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
from django.urls import path

from . import views

urlpatterns = [
    path("stop-impersonation/", views.stop_impersonation, name="stop_impersonation"),
]
