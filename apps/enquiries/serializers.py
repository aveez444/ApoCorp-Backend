# apps/enquiries/serializers.py

from rest_framework import serializers
from django.utils import timezone
from apps.customers.serializers import CustomerReadSerializer
from .models import Enquiry, EnquiryAttachment, EnquiryDelayReason
from core.mixins import CustomerLockValidationMixin


class EnquiryAttachmentSerializer(serializers.ModelSerializer):
    """Serializer for enquiry attachments with full file URL"""
    file_url = serializers.SerializerMethodField()
    
    class Meta:
        model = EnquiryAttachment
        fields = "__all__"
        read_only_fields = ("uploaded_at",)
    
    def get_file_url(self, obj):
        """Return absolute URL for the file"""
        request = self.context.get('request')
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        elif obj.file:
            return obj.file.url
        return None


class EnquiryDelayReasonSerializer(serializers.ModelSerializer):
    """Serializer for delay reasons"""
    created_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = EnquiryDelayReason
        fields = "__all__"
        read_only_fields = ("created_at", "created_by")
    
    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None


class EnquiryRevisionSerializer(serializers.ModelSerializer):
    """Serializer for enquiry revisions"""
    created_by_name = serializers.SerializerMethodField()
    assigned_to_name = serializers.SerializerMethodField()
    customer_detail = CustomerReadSerializer(source='customer', read_only=True)
    
    class Meta:
        model = Enquiry
        fields = [
            'id', 'enquiry_number', 'revision_number', 'revision_reason',
            'changed_fields', 'status', 'due_date', 'subject', 'product_name',
            'prospective_value', 'assigned_to_name', 'created_by_name', 'created_at'
        ]
    
    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None
    
    def get_assigned_to_name(self, obj):
        if obj.assigned_to:
            return obj.assigned_to.get_full_name() or obj.assigned_to.username
        return None


class EnquirySerializer(CustomerLockValidationMixin, serializers.ModelSerializer):
    """
    Write serializer for Enquiry with revision support and delay validation.
    """

    customer_detail = CustomerReadSerializer(source='customer', read_only=True)
    attachments = EnquiryAttachmentSerializer(many=True, read_only=True)
    delay_reasons = EnquiryDelayReasonSerializer(many=True, read_only=True)
    revisions = serializers.SerializerMethodField(read_only=True)
    
    regional_manager_name = serializers.SerializerMethodField(read_only=True)
    assigned_to_name = serializers.SerializerMethodField(read_only=True)
    created_by_name = serializers.SerializerMethodField(read_only=True)
    
    # Write-only fields for revision and delay
    revision_reason_input = serializers.CharField(write_only=True, required=False, allow_blank=True)
    delay_reason = serializers.CharField(write_only=True, required=False, allow_blank=True)
    is_revision = serializers.BooleanField(write_only=True, default=False)

    class Meta:
        model = Enquiry
        fields = "__all__"
        read_only_fields = (
            "tenant", "created_by", "assigned_to", "last_activity_at",
            "enquiry_number", "revision_number", "is_latest_revision",
            "parent_enquiry", "changed_fields"
        )

    def get_regional_manager_name(self, obj):
        if obj.regional_manager:
            return obj.regional_manager.get_full_name() or obj.regional_manager.username
        return None
    
    def get_assigned_to_name(self, obj):
        if obj.assigned_to:
            return obj.assigned_to.get_full_name() or obj.assigned_to.username
        return None
    
    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None
    
    def get_revisions(self, obj):
        """Get all revisions of this enquiry"""
        parent = obj.parent_enquiry or obj
        revisions = Enquiry.objects.filter(
            parent_enquiry=parent,
            is_latest_revision=False
        ).order_by('revision_number')
        return EnquiryRevisionSerializer(revisions, many=True).data

    def validate(self, attrs):
        customer = attrs.get("customer")
        if customer:
            self.validate_customer_not_locked(customer)
        
        # Validate tender fields
        enquiry_type = attrs.get("enquiry_type")
        if enquiry_type == "TENDER":
            required_tender_fields = ['emd_amount', 'dd_pbg', 'emd_due_date', 'tender_number']
            for field in required_tender_fields:
                if not attrs.get(field):
                    raise serializers.ValidationError({
                        field: f"{field} is required when enquiry type is Tender"
                    })
        
        # Check for revision
        is_revision = attrs.pop('is_revision', False)
        if is_revision:
            instance = self.instance
            if instance and not instance.can_be_revised():
                raise serializers.ValidationError(
                    f"Cannot revise enquiry with status '{instance.get_status_display()}'. "
                    "Only active enquiries can be revised."
                )
        
        # Check for delay reason if due_date is passed
        due_date = attrs.get('due_date')
        if due_date and due_date < timezone.now().date():
            delay_reason = attrs.get('delay_reason', '')
            if not delay_reason:
                raise serializers.ValidationError({
                    'delay_reason': 'Delay reason is required when due date is in the past.'
                })
        
        return attrs

    def create(self, validated_data):
        request = self.context['request']
        
        # Remove revision/delay fields that aren't model fields
        validated_data.pop('revision_reason_input', None)
        validated_data.pop('delay_reason', None)
        validated_data.pop('is_revision', None)
        
        validated_data['created_by'] = request.user
        validated_data['assigned_to'] = request.user
        validated_data['last_activity_at'] = timezone.now()
        
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context['request']
        is_revision = validated_data.pop('is_revision', False)
        revision_reason = validated_data.pop('revision_reason_input', None)
        delay_reason_text = validated_data.pop('delay_reason', None)
        
        # Check if we need to create a revision
        if is_revision and instance.can_be_revised():
            # Determine what fields are being changed
            changed_fields = {}
            for field, new_value in validated_data.items():
                old_value = getattr(instance, field)
                if old_value != new_value:
                    changed_fields[field] = {
                        'old': str(old_value) if old_value else None,
                        'new': str(new_value) if new_value else None
                    }
            
            if changed_fields:
                # Create new revision
                validated_data['changed_fields'] = changed_fields
                new_revision = instance.create_revision(
                    updated_data=validated_data,
                    changed_by=request.user,
                    reason=revision_reason
                )
                return new_revision
        
        # Regular update (not a revision)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        
        # Handle delay reason if due_date is overdue and status is being updated
        if delay_reason_text and instance.due_date and instance.due_date < timezone.now().date():
            EnquiryDelayReason.objects.create(
                enquiry=instance,
                status_update=instance.status,
                reason=delay_reason_text,
                created_by=request.user
            )
        
        instance.last_activity_at = timezone.now()
        instance.save()
        
        return instance