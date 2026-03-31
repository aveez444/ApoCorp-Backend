# apps/reports/views.py

import io

from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework.views import APIView

from core.mixins import ModelPermissionMixin
from core.viewsets import TenantModelViewSet

from .engine import ReportEngine
from .field_registry import registry_for_api
from .models import SavedReport
from .serializers import (
    RunReportSerializer,
    SavedReportSerializer,
    SavedReportListSerializer,
    ReportConfigSerializer,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Field registry  –  GET /api/reports/fields/
# ─────────────────────────────────────────────────────────────────────────────

class ReportFieldsView(APIView):
    """
    Returns the full module + field registry so the frontend can build
    the column picker and filter widgets dynamically.

    No pagination — this is a small static payload (~5 KB).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(registry_for_api())


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Ad-hoc report runner  –  POST /api/reports/run/
#                              POST /api/reports/run/?format=xlsx
# ─────────────────────────────────────────────────────────────────────────────

class RunReportView(APIView):
    """
    Execute a report config on-the-fly (without saving it).

    Request body:
        {
            "config": { modules, columns, filters, order_by },
            "page":      1,
            "page_size": 50
        }

    Add ?format=xlsx to get an Excel download instead.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RunReportSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        config    = serializer.validated_data["config"]
        page      = serializer.validated_data["page"]
        page_size = serializer.validated_data["page_size"]

        engine = ReportEngine(config=config, tenant=request.tenant)

        fmt = request.query_params.get("format", "json").lower()
        if fmt == "xlsx":
            return self._excel_response(engine, name="report")

        result = engine.get_rows(page=page, page_size=page_size)
        return Response(result)

    @staticmethod
    def _excel_response(engine: ReportEngine, name: str = "report") -> HttpResponse:
        wb     = engine.get_workbook()
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        response = HttpResponse(
            buffer.getvalue(),
            content_type=(
                "application/vnd.openxmlformats-officedocument"
                ".spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = f'attachment; filename="{name}.xlsx"'
        return response


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Saved report CRUD  –  /api/reports/saved/
# ─────────────────────────────────────────────────────────────────────────────

class SavedReportViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    CRUD for saved report definitions.

    Extra actions:
        POST  /saved/{id}/run/          – run a saved report (JSON)
        POST  /saved/{id}/run/?format=xlsx  – run + download Excel
        PATCH /saved/{id}/config/       – update only the config JSON
    """

    queryset           = SavedReport.objects.all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return SavedReportListSerializer
        return SavedReportSerializer

    def get_queryset(self):
        qs = super().get_queryset()

        # Everyone sees shared reports; owners also see their private ones
        from django.db.models import Q
        qs = qs.filter(
            Q(is_shared=True) | Q(created_by=self.request.user)
        )

        # Optional search by name
        q = self.request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(name__icontains=q)

        return qs

    def perform_create(self, serializer):
        serializer.save(
            tenant=self.request.tenant,
            created_by=self.request.user,
        )

    # ── Run a saved report ────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="run")
    def run(self, request, pk=None):
        """
        Run a saved report.
        Accepts optional pagination overrides in the request body:
            { "page": 1, "page_size": 50 }
        Add ?format=xlsx to stream an Excel file.
        """
        report    = self.get_object()
        page      = int(request.data.get("page",      1))
        page_size = min(int(request.data.get("page_size", 50)), 500)

        engine = ReportEngine(config=report.config, tenant=request.tenant)

        fmt = request.query_params.get("format", "json").lower()
        if fmt == "xlsx":
            return RunReportView._excel_response(engine, name=report.name)

        result = engine.get_rows(page=page, page_size=page_size)
        return Response(result)

    # ── Partial config update (without full PATCH on the whole object) ────────

    @action(detail=True, methods=["patch"], url_path="config")
    def update_config(self, request, pk=None):
        """
        Shortcut to update only the config JSON of a saved report.
        Body: { "config": { ... } }
        """
        report = self.get_object()

        # Ownership check — only creator can modify
        if report.created_by != request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only the report creator can modify it.")

        config_data = request.data.get("config")
        if config_data is None:
            return Response(
                {"config": "This field is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        s = ReportConfigSerializer(data=config_data)
        if not s.is_valid():
            return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)

        report.config = s.validated_data
        report.save(update_fields=["config", "updated_at"])

        return Response(SavedReportSerializer(report).data)

    # ── Duplicate a report ────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, pk=None):
        """Clone an existing saved report under a new name."""
        original = self.get_object()
        new_name = request.data.get("name", f"{original.name} (Copy)")

        clone = SavedReport.objects.create(
            tenant     = request.tenant,
            name       = new_name,
            description= original.description,
            config     = original.config,
            is_shared  = False,       # copies start as private
            created_by = request.user,
        )

        return Response(
            SavedReportSerializer(clone).data,
            status=status.HTTP_201_CREATED,
        )