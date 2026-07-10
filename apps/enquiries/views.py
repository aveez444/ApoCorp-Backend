# apps/enquiries/views.py

from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.shortcuts import get_object_or_404

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser

from .models import Enquiry, EnquiryAttachment, EnquiryDelayReason
from .serializers import (
    EnquirySerializer, EnquiryAttachmentSerializer, 
    EnquiryDelayReasonSerializer, EnquiryRevisionSerializer
)


class EnquiryViewSet(ModelPermissionMixin, TenantModelViewSet):

    queryset = Enquiry.objects.select_related('customer', 'assigned_to', 'created_by')
    serializer_class = EnquirySerializer
    permission_classes = [IsAuthenticated]

    def _get_tenant_user(self):
        return TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

    def get_queryset(self):
        queryset = super().get_queryset()
        # Only show latest revisions in main list
        queryset = queryset.filter(is_latest_revision=True)
        
        tenant_user = self._get_tenant_user()
        if tenant_user and tenant_user.role == 'employee':
            return queryset.filter(assigned_to=self.request.user)
        return queryset

    def perform_update(self, serializer):
        new_status = self.request.data.get('status')
        instance = self.get_object()
        
        # Get the actual instance (might be the original if revision is being created)
        is_revision = self.request.data.get('is_revision', False)
        
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
        
        # Check if due_date is being updated and require delay reason
        due_date = self.request.data.get('due_date')
        if due_date:
            from datetime import datetime
            try:
                due_date_obj = datetime.strptime(due_date, '%Y-%m-%d').date()
                if due_date_obj < timezone.now().date():
                    delay_reason = self.request.data.get('delay_reason', '')
                    if not delay_reason:
                        raise ValidationError({
                            'delay_reason': 'Delay reason is required when due date is in the past.'
                        })
            except ValueError:
                pass
        
        serializer.save(last_activity_at=timezone.now())

    # ─────────────────────────────────────────────────────────
    # Revision Actions
    # ─────────────────────────────────────────────────────────

    @action(detail=True, methods=['get'])
    def revisions(self, request, pk=None):
        """Get all revisions of an enquiry"""
        enquiry = self.get_object()
        parent = enquiry.parent_enquiry or enquiry
        
        revisions = Enquiry.objects.filter(
            parent_enquiry=parent
        ).order_by('revision_number')
        
        serializer = EnquiryRevisionSerializer(revisions, many=True)
        return Response({
            'current_revision': enquiry.revision_number,
            'revisions': serializer.data
        })

    @action(detail=True, methods=['post'])
    def revise(self, request, pk=None):
        """
        Create a new revision of an enquiry.
        Only allowed for active enquiries (not LOST or PO_RECEIVED).
        """
        enquiry = self.get_object()
        
        if not enquiry.can_be_revised():
            raise PermissionDenied(
                f"Cannot revise enquiry with status '{enquiry.get_status_display()}'. "
                "Only active enquiries can be revised."
            )
        
        # Get fields to update
        update_data = {}
        allowed_fields = [
            'subject', 'product_name', 'priority', 'enquiry_type', 'source_of_enquiry',
            'due_date', 'target_submission_date', 'prospective_value', 'currency',
            'region', 'regional_manager', 'rejection_reason', 'emd_amount', 'dd_pbg',
            'emd_due_date', 'tender_number', 'transaction_id', 'emd_return_amount',
            'emd_return_date', 'enquiry_date'
        ]
        
        for field in allowed_fields:
            if field in request.data:
                update_data[field] = request.data.get(field)
        
        revision_reason = request.data.get('revision_reason', '')
        if not revision_reason:
            raise ValidationError({'revision_reason': 'Reason for revision is required.'})
        
        # Create revision
        new_revision = enquiry.create_revision(
            updated_data=update_data,
            changed_by=request.user,
            reason=revision_reason
        )
        
        serializer = EnquirySerializer(new_revision, context={'request': request})
        return Response({
            'message': f'Revision R{new_revision.revision_number} created successfully',
            'enquiry': serializer.data
        })

    @action(detail=True, methods=['get'])
    def compare_revision(self, request, pk=None):
        """
        Compare two revisions of an enquiry.
        Use query params: ?from=1&to=2 (revision numbers)
        """
        enquiry = self.get_object()
        parent = enquiry.parent_enquiry or enquiry
        
        from_rev = request.query_params.get('from')
        to_rev = request.query_params.get('to')
        
        if not from_rev or not to_rev:
            raise ValidationError('Both "from" and "to" revision numbers are required.')
        
        try:
            from_revision = Enquiry.objects.get(parent_enquiry=parent, revision_number=from_rev)
            to_revision = Enquiry.objects.get(parent_enquiry=parent, revision_number=to_rev)
        except Enquiry.DoesNotExist:
            raise ValidationError('One or both revisions not found.')
        
        # Compare fields
        differences = {}
        compare_fields = [
            'subject', 'product_name', 'priority', 'status', 'enquiry_type',
            'due_date', 'target_submission_date', 'prospective_value',
            'region', 'rejection_reason'
        ]
        
        for field in compare_fields:
            old_val = getattr(from_revision, field)
            new_val = getattr(to_revision, field)
            if old_val != new_val:
                differences[field] = {
                    'old': str(old_val) if old_val else None,
                    'new': str(new_val) if new_val else None
                }
        
        return Response({
            'from_revision': from_rev,
            'to_revision': to_rev,
            'differences': differences,
            'from_data': EnquiryRevisionSerializer(from_revision).data,
            'to_data': EnquiryRevisionSerializer(to_revision).data
        })

    # ─────────────────────────────────────────────────────────
    # Delay Reason Actions
    # ─────────────────────────────────────────────────────────

    @action(detail=True, methods=['get'])
    def delay_reasons(self, request, pk=None):
        """Get all delay reasons for an enquiry"""
        enquiry = self.get_object()
        delay_reasons = enquiry.delay_reasons.all()
        serializer = EnquiryDelayReasonSerializer(delay_reasons, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def add_delay_reason(self, request, pk=None):
        """Manually add a delay reason for an enquiry"""
        enquiry = self.get_object()
        
        if not enquiry.is_overdue():
            raise ValidationError('Enquiry is not overdue. Delay reason not required.')
        
        reason = request.data.get('reason')
        if not reason:
            raise ValidationError({'reason': 'Delay reason is required.'})
        
        delay_reason = EnquiryDelayReason.objects.create(
            enquiry=enquiry,
            status_update=enquiry.status,
            reason=reason,
            created_by=request.user
        )
        
        serializer = EnquiryDelayReasonSerializer(delay_reason)
        return Response(serializer.data)

    # ─────────────────────────────────────────────────────────
    # Overdue Enquiries Report
    # ─────────────────────────────────────────────────────────

    @action(detail=False, methods=['get'])
    def overdue(self, request):
        """Get all overdue enquiries"""
        today = timezone.now().date()
        queryset = self.get_queryset().filter(
            due_date__lt=today,
            status__in=['NEW', 'NEGOTIATION']  # Only active enquiries
        ).exclude(due_date__isnull=True)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            'count': queryset.count(),
            'results': serializer.data
        })

    # ─────────────────────────────────────────────────────────
    # Reassign action
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

        # If revision is needed, create a revision for assignment change
        is_revision = request.data.get('is_revision', False)
        
        if is_revision and enquiry.can_be_revised():
            update_data = {'assigned_to': new_user}
            revision_reason = request.data.get('revision_reason', f"Reassigned to {new_user.get_full_name() or new_user.username}")
            new_revision = enquiry.create_revision(
                updated_data=update_data,
                changed_by=request.user,
                reason=revision_reason
            )
            return Response({
                "message": f"Enquiry reassigned to {new_user.get_full_name() or new_user.username} (Revision R{new_revision.revision_number})",
                "revision_created": True
            })
        else:
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
        """Get enquiry statistics."""
        queryset = self.get_queryset()
        
        stats_data = {
            "total": queryset.count(),
            "pending": queryset.filter(status='NEW').count(),
            "under_negotiation": queryset.filter(status='NEGOTIATION').count(),
            "quoted": queryset.filter(status='PO_RECEIVED').count(),
            "lost": queryset.filter(status='LOST').count(),
            "regret": queryset.filter(status='REGRET').count(),
            "total_value": queryset.aggregate(total=Sum('prospective_value'))['total'] or 0,
            "overdue": queryset.filter(
                due_date__lt=timezone.now().date(),
                status__in=['NEW', 'NEGOTIATION']
            ).exclude(due_date__isnull=True).count(),
        }
        
        if stats_data["total"] > 0:
            stats_data["pending_percentage"] = round((stats_data["pending"] / stats_data["total"]) * 100, 2)
            stats_data["quoted_percentage"] = round((stats_data["quoted"] / stats_data["total"]) * 100, 2)
            stats_data["under_negotiation_percentage"] = round((stats_data["under_negotiation"] / stats_data["total"]) * 100, 2)
            stats_data["lost_percentage"] = round((stats_data["lost"] / stats_data["total"]) * 100, 2)
        
        return Response(stats_data)