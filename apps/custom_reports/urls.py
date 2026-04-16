# apps/reports/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import ReportFieldsView, RunReportView, SavedReportViewSet
from .export_views import RunReportExcelView, SavedReportExcelView

router = DefaultRouter()
router.register(r"saved", SavedReportViewSet, basename="custom-report")

urlpatterns = [
    # Field registry
    path("fields/", ReportFieldsView.as_view(), name="custom-report-fields"),
    
    # Ad-hoc run (JSON)
    path("run/", RunReportView.as_view(), name="custom-report-run"),
    
    # Excel export endpoints
    path("run-excel/", RunReportExcelView.as_view(), name="custom-report-run-excel"),
    path("saved/<uuid:report_id>/export-excel/", SavedReportExcelView.as_view(), name="saved-report-export-excel"),
    
    # Saved report CRUD
    path("", include(router.urls)),
]