from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from django.utils import timezone
from core.viewsets import TenantModelViewSet
from rest_framework.parsers import MultiPartParser, FormParser
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser
from .models import Quotation, QuotationAttachment
from .serializers import QuotationSerializer, QuotationAttachmentSerializer


from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser
from .models import Quotation, QuotationAttachment
from .serializers import QuotationSerializer, QuotationAttachmentSerializer


class QuotationViewSet(ModelPermissionMixin, TenantModelViewSet):

    queryset = Quotation.objects.all()
    serializer_class = QuotationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()

        tenant_user = TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

        # Employees see quotations of enquiries assigned to them
        if tenant_user and tenant_user.role == "employee":
            return queryset.filter(enquiry__assigned_to=self.request.user)

        return queryset

    def _require_manager(self, request):
        tenant_user = TenantUser.objects.filter(
            user=request.user,
            tenant=request.tenant
        ).first()
        if not tenant_user or tenant_user.role != "manager":
            raise PermissionDenied("Only managers are allowed to perform this action")

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        quotation = self.get_object()
        self._require_manager(request)

        quotation.review_status = "APPROVED"
        quotation.visibility = "EXTERNAL"
        quotation.client_status = "DRAFT"  # ← ADD THIS
        quotation.save(update_fields=["review_status", "visibility", "client_status"])

        return Response({"message": "Quotation approved"})

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        quotation = self.get_object()
        self._require_manager(request)

        quotation.review_status = "REJECTED"
        quotation.manager_remark = request.data.get("manager_remark", "")
        quotation.rejected_at = timezone.now()
        quotation.visibility = "INTERNAL"
        quotation.save(update_fields=["review_status", "manager_remark", "rejected_at", "visibility"])

        return Response({"message": "Quotation rejected"})

    @action(detail=True, methods=["post"])
    def send_to_client(self, request, pk=None):
        quotation = self.get_object()

        if quotation.review_status != "APPROVED":
            return Response({"error": "Quotation must be approved first"}, status=400)

        quotation.client_status = "SENT"
        quotation.save(update_fields=["client_status"])

        return Response({"message": "Quotation sent to client"})

    @action(detail=False, methods=["get"])
    def dashboard_stats(self, request):
        return Response({
            "under_review": Quotation.objects.filter(review_status="UNDER_REVIEW").count(),
            "approved":     Quotation.objects.filter(review_status="APPROVED").count(),
            "rejected":     Quotation.objects.filter(review_status="REJECTED").count(),
            "accepted":     Quotation.objects.filter(client_status="ACCEPTED").count(),
            "negotiation":  Quotation.objects.filter(client_status="UNDER_NEGOTIATION").count(),
        })

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def upload_file(self, request, pk=None):
        quotation = self.get_object()

        if 'file' not in request.FILES:
            return Response({"error": "No file provided"}, status=400)

        attachment = QuotationAttachment.objects.create(
            quotation=quotation,
            file=request.FILES['file']
        )

        serializer = QuotationAttachmentSerializer(attachment)
        return Response(serializer.data, status=201)

    @action(detail=True, methods=["get"])
    def attachments(self, request, pk=None):
        quotation = self.get_object()
        serializer = QuotationAttachmentSerializer(quotation.attachments.all(), many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["delete"], url_path="attachments/(?P<attachment_id>[^/.]+)")
    def delete_attachment(self, request, pk=None, attachment_id=None):
        quotation = self.get_object()
        try:
            attachment = quotation.attachments.get(id=attachment_id)
            attachment.file.delete()
            attachment.delete()
            return Response(status=204)
        except QuotationAttachment.DoesNotExist:
            return Response({"error": "Attachment not found"}, status=404)

    @action(detail=True, methods=["post"])
    def mark_negotiating(self, request, pk=None):
        """Move quotation to UNDER_NEGOTIATION"""
        quotation = self.get_object()
        quotation.client_status = "UNDER_NEGOTIATION"
        quotation.save(update_fields=["client_status"])
        return Response({"message": "Marked as under negotiation"})

    @action(detail=True, methods=["post"])
    def mark_accepted(self, request, pk=None):
        """Client accepted the quotation"""
        quotation = self.get_object()
        quotation.client_status = "ACCEPTED"
        quotation.save(update_fields=["client_status"])
        return Response({"message": "Quotation accepted by client"})

    @action(detail=True, methods=["post"])
    def mark_rejected(self, request, pk=None):
        """Client rejected the quotation"""
        quotation = self.get_object()
        quotation.client_status = "REJECTED_BY_CLIENT"
        quotation.manager_remark = request.data.get("remark", "")  # Optional: add rejection reason
        quotation.save(update_fields=["client_status", "manager_remark"])
        return Response({"message": "Quotation rejected by client"})        

    def perform_update(self, serializer):
        """
        Controls who can update quotations.
        Only managers can update quotations not assigned to them.
        Employees can only update quotations assigned to them.
        """
        quotation = self.get_object()
        current_user = self.request.user
        
        # Get tenant user role
        tenant_user = TenantUser.objects.filter(
            user=current_user,
            tenant=self.request.tenant
        ).first()
        
        # Check if employee is trying to update someone else's quotation
        if tenant_user and tenant_user.role == "employee":
            # Employees can only update quotations assigned to them
            if quotation.enquiry.assigned_to != current_user:
                raise PermissionDenied(
                    "You can only update quotations assigned to you."
                )
        
        # Fields that should NOT reset approval status (metadata only)
        metadata_fields = ['po_number', 'manager_remark', 'rejected_at', 'follow_ups', 'currency']
        
        # Check if ONLY metadata fields are being updated
        updated_fields = set(self.request.data.keys())
        
        # Remove nested fields from consideration
        if 'line_items' in updated_fields:
            updated_fields.remove('line_items')
        if 'terms' in updated_fields:
            updated_fields.remove('terms')
        if 'follow_ups' in updated_fields:
            updated_fields.remove('follow_ups')
        
        # Check if any remaining fields are NOT metadata
        non_metadata_fields = updated_fields - set(metadata_fields)
        
        # If it's after approval and we're updating more than just metadata → reset
        if quotation.review_status == "APPROVED" and non_metadata_fields:
            # Critical update - reset to under review
            serializer.save(
                review_status="UNDER_REVIEW",
                visibility="INTERNAL"
            )
            # Update enquiry status back to NEGOTIATION
            quotation.enquiry.status = "NEGOTIATION"
            quotation.enquiry.save(update_fields=["status"])
        else:
            # For metadata-only updates (like PO number), keep the status
            serializer.save()