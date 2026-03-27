from rest_framework import serializers
from .models import VisitReport, VisitReportAttachment


class VisitReportAttachmentSerializer(serializers.ModelSerializer):

    file_size_display = serializers.SerializerMethodField()

    class Meta:
        model = VisitReportAttachment
        fields = ('id', 'file', 'file_name', 'file_size', 'file_size_display', 'uploaded_at')

    def get_file_size_display(self, obj):
        size = obj.file_size or 0
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size // 1024} KB"
        else:
            return f"{round(size / (1024 * 1024), 1)} MB"


class VisitReportSerializer(serializers.ModelSerializer):

    attachments = VisitReportAttachmentSerializer(many=True, read_only=True)
    created_by_name = serializers.CharField(
        source='created_by.get_full_name', read_only=True
    )

    class Meta:
        model = VisitReport
        fields = '__all__'
        read_only_fields = (
            'tenant',
            'visit_number',
            'created_by',
            'created_at',
            'updated_at',
        )


class VisitReportListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views."""

    class Meta:
        model = VisitReport
        fields = (
            'id', 'visit_number', 'date', 'type_of_report',
            'company_name', 'department', 'author',
            'attendants', 'subject', 'agenda', 'created_at',
        )