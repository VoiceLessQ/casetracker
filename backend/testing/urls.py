from django.urls import path

from . import views

urlpatterns = [
    path("stop-impersonation/", views.stop_impersonation, name="stop_impersonation"),
]
