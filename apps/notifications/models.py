import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone
from core.mixins import TenantModelMixin


class Notification(TenantModelMixin):

    TYPE_CHOICES = [
        ('INFO', 'Info'),
        ('WARNING', 'Warning'),
        ('SUCCESS', 'Success'),
        ('ALERT', 'Alert'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    title = models.CharField(max_length=255)
    message = models.TextField()

    type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default='INFO'
    )

    link = models.CharField(
        max_length=500,
        blank=True,
        help_text="Frontend route to redirect when clicked"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_notifications'
    )

    is_broadcast = models.BooleanField(
        default=False,
        help_text="If true, send to all employees"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class NotificationRecipient(models.Model):

    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name='recipients'
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications'
    )

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('notification', 'user')

    def mark_as_read(self):
        self.is_read = True
        self.read_at = timezone.now()
        self.save()