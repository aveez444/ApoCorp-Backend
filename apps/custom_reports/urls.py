from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import ReportFieldsView, RunReportView, SavedReportViewSet

router = DefaultRouter()
router.register(r"saved", SavedReportViewSet, basename="custom-report")

urlpatterns = [
    # Field registry — used by frontend to build the column picker UI
    # GET  /api/custom-reports/fields/
    path("fields/", ReportFieldsView.as_view(), name="custom-report-fields"),

    # Ad-hoc run — execute a config without saving
    # POST /api/custom-reports/run/
    # POST /api/custom-reports/run/?format=xlsx
    path("run/", RunReportView.as_view(), name="custom-report-run"),

    # Saved report CRUD + run actions
    # GET    /api/custom-reports/saved/
    # POST   /api/custom-reports/saved/
    # GET    /api/custom-reports/saved/{id}/
    # PUT    /api/custom-reports/saved/{id}/
    # PATCH  /api/custom-reports/saved/{id}/
    # DELETE /api/custom-reports/saved/{id}/
    # POST   /api/custom-reports/saved/{id}/run/
    # POST   /api/custom-reports/saved/{id}/run/?format=xlsx
    # PATCH  /api/custom-reports/saved/{id}/config/
    # POST   /api/custom-reports/saved/{id}/duplicate/
    path("", include(router.urls)),
]