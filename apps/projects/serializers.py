# apps/projects/serializers.py
from django.utils import timezone
from rest_framework import serializers
from .models import Project, ProjectCostEntry, ProjectMilestone, ProjectDocument


class ProjectCostEntrySerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ProjectCostEntry
        fields = '__all__'
        read_only_fields = ('project', 'recorded_at', 'recorded_by')

    def get_recorded_by_name(self, obj):
        if obj.recorded_by:
            return obj.recorded_by.get_full_name() or obj.recorded_by.username
        return None


class ProjectMilestoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectMilestone
        fields = '__all__'
        read_only_fields = ('project',)

    def validate(self, attrs):
        # Auto-stamp completed_date the moment is_completed flips true,
        # unless the caller already supplied one explicitly.
        if attrs.get('is_completed') and not attrs.get('completed_date'):
            attrs['completed_date'] = timezone.now().date()
        return attrs


class ProjectDocumentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ProjectDocument
        fields = '__all__'
        read_only_fields = ('project', 'uploaded_by', 'uploaded_at')

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return None


class ProjectListSerializer(serializers.ModelSerializer):
    """Flat lightweight serializer for list views."""
    customer_name = serializers.CharField(source='customer.company_name', read_only=True)
    project_manager_name = serializers.SerializerMethodField()
    total_cost = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    margin_pct = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            'id', 'project_number', 'name', 'customer', 'customer_name',
            'status', 'contract_value', 'currency', 'start_date', 'end_date',
            'project_manager', 'project_manager_name', 'total_cost',
            'margin_pct', 'created_at',
        ]

    def get_project_manager_name(self, obj):
        if obj.project_manager:
            return obj.project_manager.get_full_name() or obj.project_manager.username
        return None

    def get_margin_pct(self, obj):
        return round(obj.margin_pct, 2)


class ProjectDetailSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.company_name', read_only=True)

    # FIXED: Project.bom no longer exists (replaced by active_package long ago).
    # The old `bom_number = source='bom.bom_number'` line was a live AttributeError
    # waiting to happen on every GET of a project detail.
    active_package_number = serializers.CharField(source='active_package.package_number', read_only=True)
    active_package_version = serializers.CharField(source='active_package.version', read_only=True)
    active_package_status = serializers.CharField(source='active_package.status', read_only=True)

    sales_order_number = serializers.CharField(source='sales_order.order_number', read_only=True)
    milestones = ProjectMilestoneSerializer(many=True, read_only=True)
    documents = ProjectDocumentSerializer(many=True, read_only=True)
    total_cost = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    gross_profit = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    margin_pct = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = '__all__'
        read_only_fields = (
            'tenant', 'project_number', 'procurement_cost', 'inventory_cost',
            # FIXED: active_package must only change via
            # apps.engineering.services.accept_package() — never a raw PATCH.
            'active_package',
            'created_at', 'updated_at',
        )

    def get_margin_pct(self, obj):
        return round(obj.margin_pct, 2)