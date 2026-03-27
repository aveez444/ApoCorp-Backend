# apps/enquiries/serializers.py

from rest_framework import serializers
from django.utils import timezone
from apps.customers.serializers import CustomerReadSerializer
from .models import Enquiry, EnquiryAttachment
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


class EnquirySerializer(CustomerLockValidationMixin, serializers.ModelSerializer):
    """
    Write serializer for Enquiry.
    - assigned_to and created_by are set server-side on create.
    - Manager can update assigned_to via the dedicated assign/ action or PATCH.
    - customer is a FK write field (pass customer UUID on create).
    - region is a choice field (NORTH / SOUTH / EAST / WEST / CENTRAL).
    - regional_manager is a writable FK – pass a User PK; employee fills this
      from the all-users dropdown (/api/accounts/users/).
    - Tender fields are included conditionally.
    """

    # Read-only nested customer detail – frontend gets full customer info without snapshot
    customer_detail = CustomerReadSerializer(source='customer', read_only=True)
    
    # Include attachments with full URLs
    attachments = EnquiryAttachmentSerializer(many=True, read_only=True)

    # Human-readable display for regional_manager on reads
    regional_manager_name = serializers.SerializerMethodField(read_only=True)
    
    # Add human-readable display for assigned_to
    assigned_to_name = serializers.SerializerMethodField(read_only=True)
    
    # Add human-readable display for created_by
    created_by_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Enquiry
        fields = "__all__"
        read_only_fields = (
            "tenant",
            "created_by",
            "assigned_to",
            "last_activity_at",
            "enquiry_number",
        )

    def get_regional_manager_name(self, obj):
        if obj.regional_manager:
            return obj.regional_manager.get_full_name() or obj.regional_manager.username
        return None
    
    def get_assigned_to_name(self, obj):
        """Get assigned user's full name"""
        if obj.assigned_to:
            return obj.assigned_to.get_full_name() or obj.assigned_to.username
        return None
    
    def get_created_by_name(self, obj):
        """Get creator's full name"""
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None

    def validate(self, attrs):
        customer = attrs.get("customer")
        if customer:
            self.validate_customer_not_locked(customer)
        
        # Validate tender fields if enquiry_type is TENDER
        enquiry_type = attrs.get("enquiry_type")
        if enquiry_type == "TENDER":
            # Required fields for tender
            required_tender_fields = ['emd_amount', 'dd_pbg', 'emd_due_date', 'tender_number']
            for field in required_tender_fields:
                if not attrs.get(field):
                    raise serializers.ValidationError({
                        field: f"{field} is required when enquiry type is Tender"
                    })
        
        return attrs

    def create(self, validated_data):
        request = self.context['request']
        validated_data['created_by'] = request.user
        validated_data['assigned_to'] = request.user
        validated_data['last_activity_at'] = timezone.now()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data['last_activity_at'] = timezone.now()
        return super().update(instance, validated_data)