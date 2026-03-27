import uuid
from django.db import models
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin


class VisitReport(TenantModelMixin):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    visit_number = models.CharField(max_length=50, unique=True, blank=True)

    date = models.DateField()
    type_of_report = models.CharField(max_length=100, blank=True)
    company_name = models.CharField(max_length=255, blank=True)
    department = models.CharField(max_length=150, blank=True)
    author = models.CharField(max_length=150, blank=True)
    attendants = models.CharField(max_length=255, blank=True)
    subject = models.CharField(max_length=255, blank=True)
    agenda = models.TextField(blank=True)
    details_of_meeting = models.TextField(blank=True)
    remarks = models.TextField(blank=True)

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='visit_reports'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.visit_number or str(self.id)

    def save(self, *args, **kwargs):
        if not self.visit_number:
            from django.utils import timezone
            ts = timezone.now().strftime('%Y%m%d%H%M%S')
            self.visit_number = f"VR-{ts}"
        super().save(*args, **kwargs)


class VisitReportAttachment(models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    visit_report = models.ForeignKey(
        VisitReport, on_delete=models.CASCADE, related_name='attachments'
    )
    file = models.FileField(upload_to='visit_reports/attachments/')
    file_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(default=0)   # bytes
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.file and not self.file_name:
            self.file_name = self.file.name.split('/')[-1]
        if self.file and not self.file_size:
            try:
                self.file_size = self.file.size
            except Exception:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        return self.file_name or str(self.id)