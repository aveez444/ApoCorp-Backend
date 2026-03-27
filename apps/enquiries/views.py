from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Sum

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser

from .models import Enquiry, EnquiryAttachment
from .serializers import EnquirySerializer, EnquiryAttachmentSerializer


class EnquiryViewSet(ModelPermissionMixin, TenantModelViewSet):

    queryset = Enquiry.objects.select_related('customer', 'assigned_to', 'created_by')
    serializer_class = EnquirySerializer
    permission_classes = [IsAuthenticated]

    # ─────────────────────────────────────────────────────────
    # Visibility: employees only see their own assigned enquiries
    # ─────────────────────────────────────────────────────────

    def _get_tenant_user(self):
        return TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

    def get_queryset(self):
        queryset = super().get_queryset()
        tenant_user = self._get_tenant_user()
        if tenant_user and tenant_user.role == 'employee':
            return queryset.filter(assigned_to=self.request.user)
        return queryset

    def perform_update(self, serializer):
        new_status = self.request.data.get('status')
        instance = self.get_object()

        if new_status and new_status != instance.status:
            allowed_transitions = {
                'NEW': ['NEGOTIATION', 'LOST', 'REGRET'],
                'NEGOTIATION': ['PO_RECEIVED', 'LOST'],
                'PO_RECEIVED': [],
                'LOST': [],
                'REGRET': [],
            }
            if new_status not in allowed_transitions.get(instance.status, []):
                raise PermissionDenied(
                    f"Invalid status transition from {instance.status} to {new_status}"
                )

        serializer.save(last_activity_at=timezone.now())

    # ─────────────────────────────────────────────────────────
    # Reassign action – manager only.
    # Changing assigned_to here propagates visibility to
    # all downstream objects (quotation, OA, order) automatically
    # because they all filter via enquiry__assigned_to.
    # ─────────────────────────────────────────────────────────

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        tenant_user = self._get_tenant_user()
        if not tenant_user or tenant_user.role != 'manager':
            raise PermissionDenied("Only managers can reassign enquiries.")

        enquiry = self.get_object()
        user_id = request.data.get('assigned_to')
        if not user_id:
            raise ValidationError({"assigned_to": "This field is required."})

        from django.contrib.auth.models import User
        try:
            new_user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise ValidationError({"assigned_to": "User not found."})

        enquiry.assigned_to = new_user
        enquiry.last_activity_at = timezone.now()
        enquiry.save(update_fields=['assigned_to', 'last_activity_at'])

        return Response({
            "message": f"Enquiry reassigned to {new_user.get_full_name() or new_user.username}"
        })

    # ─────────────────────────────────────────────────────────
    # File upload
    # ─────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def upload_file(self, request, pk=None):
        enquiry = self.get_object()
        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"error": "No file provided"}, status=400)

        attachment = EnquiryAttachment.objects.create(enquiry=enquiry, file=file_obj)
        enquiry.last_activity_at = timezone.now()
        enquiry.save(update_fields=['last_activity_at'])

        return Response(EnquiryAttachmentSerializer(attachment).data)

    # ─────────────────────────────────────────────────────────
    # Stats
    # ─────────────────────────────────────────────────────────

    @action(detail=False, methods=['get'])
    def stats(self, request):
        queryset = self.get_queryset()
        return Response({
            "total": queryset.count(),
            "pending": queryset.filter(status='NEW').count(),
            "under_negotiation": queryset.filter(status='NEGOTIATION').count(),
            "po_received": queryset.filter(status='PO_RECEIVED').count(),
            "lost": queryset.filter(status='LOST').count(),
            "total_value": queryset.aggregate(total=Sum('prospective_value'))['total'] or 0,
        })