# apps/qc/serializers.py

from rest_framework import serializers
from django.db import transaction

from .models import (
    InspectionPlan, InspectionParameter,
    QCInspectionOrder, QCResult, QCAttachment,
    NCR,
)


def _user_name(user):
    if user:
        return user.get_full_name() or user.username
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Inspection Plan + Parameters
# ─────────────────────────────────────────────────────────────────────────────

class InspectionParameterSerializer(serializers.ModelSerializer):
    class Meta:
        model  = InspectionParameter
        fields = '__all__'
        read_only_fields = ('plan',)


class InspectionPlanSerializer(serializers.ModelSerializer):
    parameters   = InspectionParameterSerializer(many=True)
    item_code    = serializers.CharField(source='item.item_code', read_only=True)
    item_name    = serializers.CharField(source='item.name',      read_only=True)

    class Meta:
        model  = InspectionPlan
        fields = '__all__'
        read_only_fields = ('tenant',)

    def validate(self, attrs):
        if not attrs.get('item') and not attrs.get('item_category'):
            raise serializers.ValidationError(
                "Either item or item_category must be provided."
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        params_data = validated_data.pop('parameters', [])
        plan = InspectionPlan.objects.create(**validated_data)
        for p in params_data:
            InspectionParameter.objects.create(plan=plan, **p)
        return plan

    @transaction.atomic
    def update(self, instance, validated_data):
        params_data = validated_data.pop('parameters', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if params_data is not None:
            instance.parameters.all().delete()
            for p in params_data:
                InspectionParameter.objects.create(plan=instance, **p)
        return instance


# ─────────────────────────────────────────────────────────────────────────────
# QC Results + Attachments
# ─────────────────────────────────────────────────────────────────────────────

class QCResultSerializer(serializers.ModelSerializer):
    parameter_name = serializers.CharField(source='parameter.parameter_name', read_only=True)
    parameter_type = serializers.CharField(source='parameter.parameter_type', read_only=True)
    min_value      = serializers.DecimalField(source='parameter.min_value',
                                              max_digits=15, decimal_places=4,
                                              read_only=True)
    max_value      = serializers.DecimalField(source='parameter.max_value',
                                              max_digits=15, decimal_places=4,
                                              read_only=True)
    acceptance_criteria = serializers.CharField(source='parameter.acceptance_criteria',
                                                read_only=True)

    class Meta:
        model  = QCResult
        fields = '__all__'
        read_only_fields = ('inspection_order',)


class QCAttachmentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model  = QCAttachment
        fields = '__all__'
        read_only_fields = ('inspection_order', 'uploaded_at')

    def get_file_url(self, obj):
        request = self.context.get('request')
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        return obj.file.url if obj.file else None


# ─────────────────────────────────────────────────────────────────────────────
# QC Inspection Order
# ─────────────────────────────────────────────────────────────────────────────

class QCInspectionOrderListSerializer(serializers.ModelSerializer):
    """Flat lightweight serializer for list views."""
    item_code      = serializers.CharField(source='item.item_code', read_only=True)
    item_name      = serializers.CharField(source='item.name',      read_only=True)
    inspector_name = serializers.SerializerMethodField()
    batch_number   = serializers.SerializerMethodField()

    class Meta:
        model  = QCInspectionOrder
        fields = [
            'id', 'qc_number', 'qc_type', 'reference_type', 'reference_id',
            'item', 'item_code', 'item_name', 'batch', 'batch_number',
            'inspector', 'inspector_name', 'status', 'outcome',
            'sample_qty', 'inspected_qty', 'passed_qty', 'failed_qty',
            'started_at', 'completed_at', 'created_at',
        ]

    def get_inspector_name(self, obj):
        return _user_name(obj.inspector)

    def get_batch_number(self, obj):
        return obj.batch.batch_number if obj.batch else None


class QCInspectionOrderDetailSerializer(serializers.ModelSerializer):
    """Full serializer with nested results, attachments, and plan parameters."""
    item_code      = serializers.CharField(source='item.item_code', read_only=True)
    item_name      = serializers.CharField(source='item.name',      read_only=True)
    inspector_name = serializers.SerializerMethodField()
    batch_number   = serializers.SerializerMethodField()
    results        = QCResultSerializer(many=True, read_only=True)
    attachments    = QCAttachmentSerializer(many=True, read_only=True)
    plan_parameters = serializers.SerializerMethodField()

    # GRN context (for inward QC)
    grn_number     = serializers.SerializerMethodField()
    vendor_name    = serializers.SerializerMethodField()

    class Meta:
        model  = QCInspectionOrder
        fields = '__all__'
        read_only_fields = (
            'qc_number', 'tenant', 'status', 'outcome',
            'passed_qty', 'failed_qty', 'started_at', 'completed_at',
        )

    def get_inspector_name(self, obj):
        return _user_name(obj.inspector)

    def get_batch_number(self, obj):
        return obj.batch.batch_number if obj.batch else None

    def get_plan_parameters(self, obj):
        """Return plan parameters so the inspector sees what to check."""
        if obj.plan:
            return InspectionParameterSerializer(
                obj.plan.parameters.order_by('sequence'), many=True
            ).data
        return []

    def get_grn_number(self, obj):
        if obj.grn_item:
            return obj.grn_item.grn.grn_number
        return None

    def get_vendor_name(self, obj):
        if obj.grn_item:
            return obj.grn_item.grn.vendor.name
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Close inspection input serializer
# ─────────────────────────────────────────────────────────────────────────────

class CloseInspectionSerializer(serializers.Serializer):
    """
    Input body for POST /qc/inspection-orders/{id}/close/
    """
    outcome  = serializers.ChoiceField(choices=['PASS', 'FAIL', 'HOLD'])
    remarks  = serializers.CharField(required=False, allow_blank=True, default='')
    results  = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list,
        help_text="[{parameter_id, measured_value, status, remarks}]"
    )
    # For inward QC partial acceptance (optional — default is full lot accepted/rejected)
    accepted_qty = serializers.DecimalField(
        max_digits=15, decimal_places=3, required=False, allow_null=True, default=None
    )


# ─────────────────────────────────────────────────────────────────────────────
# NCR
# ─────────────────────────────────────────────────────────────────────────────

class NCRSerializer(serializers.ModelSerializer):
    raised_by_name      = serializers.SerializerMethodField()
    disposition_by_name = serializers.SerializerMethodField()
    qc_number           = serializers.CharField(
        source='inspection_order.qc_number', read_only=True
    )
    item_code           = serializers.CharField(
        source='inspection_order.item.item_code', read_only=True
    )
    vendor_name         = serializers.SerializerMethodField()

    class Meta:
        model  = NCR
        fields = '__all__'
        read_only_fields = (
            'ncr_number', 'tenant', 'raised_by',
            'disposition_by', 'disposition_at', 'created_at',
        )

    def get_raised_by_name(self, obj):
        return _user_name(obj.raised_by)

    def get_disposition_by_name(self, obj):
        return _user_name(obj.disposition_by)

    def get_vendor_name(self, obj):
        try:
            return obj.inspection_order.grn_item.grn.vendor.name
        except AttributeError:
            return None