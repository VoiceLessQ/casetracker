from django.contrib import admin
from django.urls import include, path

from cases import views as cases_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("dashboard/", cases_views.dashboard, name="dashboard"),
    path("", include("testing.urls")),
]
