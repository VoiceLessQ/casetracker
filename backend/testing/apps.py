# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
from django.apps import AppConfig


class TestingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "testing"
