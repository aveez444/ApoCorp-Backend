from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser
from .models import Notification, NotificationRecipient
from .serializers import NotificationSerializer

User = get_user_model()


class NotificationViewSet(ModelPermissionMixin, TenantModelViewSet):

    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        tenant_user = TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

        if tenant_user and tenant_user.role == 'manager':
            # Managers see notifications they created (for history panel)
            return Notification.objects.filter(
                tenant=self.request.tenant,
                created_by=self.request.user
            ).order_by('-created_at')

        # Employees only see notifications addressed to them
        return Notification.objects.filter(
            recipients__user=self.request.user
        ).order_by('-created_at')

    def perform_create(self, serializer):

        tenant_user = TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

        if not tenant_user or tenant_user.role != 'manager':
            raise PermissionDenied("Only managers can send notifications.")

        notification = serializer.save(
            tenant=self.request.tenant,
            created_by=self.request.user
        )

        if notification.is_broadcast:
            employees = TenantUser.objects.filter(
                tenant=self.request.tenant,
                role='employee',
                is_active=True
            ).values_list('user', flat=True)

            NotificationRecipient.objects.bulk_create([
                NotificationRecipient(notification=notification, user_id=uid)
                for uid in employees
            ], ignore_conflicts=True)

        else:
            user_ids = self.request.data.get("recipient_ids", [])
            NotificationRecipient.objects.bulk_create([
                NotificationRecipient(notification=notification, user_id=uid)
                for uid in user_ids
            ], ignore_conflicts=True)

    # ── Mark a single notification as read (employee) ──────────────────────
    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        recipient = notification.recipients.filter(user=request.user).first()
        if recipient:
            recipient.mark_as_read()
        return Response({"message": "Marked as read"})

    # ── Unread count badge ──────────────────────────────────────────────────
    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        count = NotificationRecipient.objects.filter(
            user=request.user,
            is_read=False,
            notification__tenant=request.tenant
        ).count()
        return Response({"unread_count": count})

    # ── Manager: list of notifications this manager sent ───────────────────
    @action(detail=False, methods=['get'])
    def sent(self, request):
        tenant_user = TenantUser.objects.filter(
            user=request.user,
            tenant=request.tenant
        ).first()

        if not tenant_user or tenant_user.role != 'manager':
            raise PermissionDenied("Only managers can view sent notifications.")

        notifications = Notification.objects.filter(
            tenant=request.tenant,
            created_by=request.user
        ).prefetch_related('recipients').order_by('-created_at')

        serializer = self.get_serializer(notifications, many=True)
        return Response(serializer.data)