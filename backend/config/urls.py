# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
from django.contrib import admin
from django.urls import include, path

from cases import views as cases_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),   # set_language (runtime language switch)
    path("dashboard/", cases_views.dashboard, name="dashboard"),
    path("", include("testing.urls")),
]
