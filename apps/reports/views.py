from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser

from .models import VisitReport, VisitReportAttachment
from .serializers import (
    VisitReportSerializer,
    VisitReportListSerializer,
    VisitReportAttachmentSerializer,
)


class VisitReportViewSet(ModelPermissionMixin, TenantModelViewSet):

    queryset = VisitReport.objects.all()
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_serializer_class(self):
        if self.action == 'list':
            return VisitReportListSerializer
        return VisitReportSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        tenant_user = TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

        # Employees only see their own reports
        if tenant_user and tenant_user.role == 'employee':
            queryset = queryset.filter(created_by=self.request.user)

        # Search
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                company_name__icontains=search
            ) | queryset.filter(
                visit_number__icontains=search
            ) | queryset.filter(
                author__icontains=search
            )

        return queryset

    def perform_create(self, serializer):
        serializer.save(
            tenant=self.request.tenant,
            created_by=self.request.user,
        )

    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def upload_attachment(self, request, pk=None):
        visit_report = self.get_object()
        files = request.FILES.getlist('files')

        if not files:
            return Response({'error': 'No files provided.'}, status=400)

        created = []
        for f in files:
            attachment = VisitReportAttachment.objects.create(
                visit_report=visit_report,
                file=f,
                file_name=f.name,
                file_size=f.size,
            )
            created.append(VisitReportAttachmentSerializer(attachment, context={'request': request}).data) 

        return Response({'attachments': created}, status=201)

    @action(detail=True, methods=['delete'], url_path='attachments/(?P<attachment_id>[^/.]+)')
    def delete_attachment(self, request, pk=None, attachment_id=None):
        visit_report = self.get_object()
        try:
            attachment = visit_report.attachments.get(id=attachment_id)
            attachment.file.delete(save=False)
            attachment.delete()
            return Response({'message': 'Attachment deleted.'})
        except VisitReportAttachment.DoesNotExist:
            return Response({'error': 'Attachment not found.'}, status=404)