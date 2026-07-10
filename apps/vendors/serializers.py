# apps/vendors/serializers.py

from rest_framework import serializers
from django.db import transaction
from django.utils import timezone

from .models import (
    Vendor, VendorContact, VendorBankDetail,
    VendorAddress, VendorDocument, ApprovedVendorList,
)


# ─────────────────────────────────────────────────────────────────────────────
# Sub-record serializers
# ─────────────────────────────────────────────────────────────────────────────

class VendorContactSerializer(serializers.ModelSerializer):
    class Meta:
        model  = VendorContact
        fields = '__all__'
        read_only_fields = ('vendor',)


class VendorBankDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model  = VendorBankDetail
        fields = '__all__'
        read_only_fields = ('vendor',)


class VendorAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model  = VendorAddress
        fields = '__all__'
        read_only_fields = ('vendor',)


class VendorDocumentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model  = VendorDocument
        fields = '__all__'
        read_only_fields = ('vendor', 'uploaded_at')

    def get_file_url(self, obj):
        request = self.context.get('request')
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        return obj.file.url if obj.file else None


# ─────────────────────────────────────────────────────────────────────────────
# Vendor list serializer — flat, no nested children (fast for list views)
# ─────────────────────────────────────────────────────────────────────────────

class VendorListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for list views and FK pickers (e.g. RFQ vendor picker).
    Does not include contacts / addresses / documents to keep payload small.
    """
    primary_contact_name  = serializers.SerializerMethodField()
    primary_contact_email = serializers.SerializerMethodField()
    approved_by_name      = serializers.SerializerMethodField()
    created_by_name       = serializers.SerializerMethodField()

    class Meta:
        model  = Vendor
        fields = [
            'id', 'vendor_code', 'legacy_vendor_code', 'name',
            'vendor_type', 'category', 'gstin', 'pan',
            'msme_registered', 'currency', 'credit_days', 'lead_time_days',
            'payment_terms', 'rating', 'status', 'is_approved',
            'approved_by', 'approved_by_name', 'approved_at',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
            'primary_contact_name', 'primary_contact_email',
        ]
        read_only_fields = ('vendor_code', 'tenant', 'approved_by', 'approved_at',
                            'created_by', 'created_at', 'updated_at')

    def get_primary_contact_name(self, obj):
        c = obj.contacts.filter(is_primary=True).first()
        return c.name if c else None

    def get_primary_contact_email(self, obj):
        c = obj.contacts.filter(is_primary=True).first()
        return c.email if c else None

    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return obj.approved_by.get_full_name() or obj.approved_by.username
        return None

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Vendor detail serializer — nested, used for create / retrieve / update
# ─────────────────────────────────────────────────────────────────────────────

class VendorDetailSerializer(serializers.ModelSerializer):
    """
    Full serializer with nested contacts, addresses, bank_details, documents.

    On CREATE:  pass nested arrays; all created atomically.
    On UPDATE:  nested arrays are replaced (delete + recreate).
                Omitting a nested key entirely = no change to that relation.
                Passing an empty list = delete all records for that relation.
    """

    contacts     = VendorContactSerializer(many=True, required=False)
    addresses    = VendorAddressSerializer(many=True, required=False)
    bank_details = VendorBankDetailSerializer(many=True, required=False)
    documents    = VendorDocumentSerializer(many=True, read_only=True)
    # Documents are uploaded separately via POST /vendors/{id}/upload-document/
    # to allow multipart file uploads without mixing with JSON payloads.

    approved_by_name = serializers.SerializerMethodField()
    created_by_name  = serializers.SerializerMethodField()

    class Meta:
        model  = Vendor
        fields = '__all__'
        read_only_fields = (
            'vendor_code', 'tenant', 'is_approved',
            'approved_by', 'approved_by_name', 'approved_at',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
            'blacklist_reason',  # set only via /blacklist/ action
        )

    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return obj.approved_by.get_full_name() or obj.approved_by.username
        return None

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None

    def validate_rating(self, value):
        if value < 0 or value > 5:
            raise serializers.ValidationError("Rating must be between 0 and 5.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        contacts_data     = validated_data.pop('contacts', [])
        addresses_data    = validated_data.pop('addresses', [])
        bank_details_data = validated_data.pop('bank_details', [])

        request = self.context.get('request')
        if request:
            validated_data['created_by'] = request.user

        vendor = Vendor.objects.create(**validated_data)

        for c in contacts_data:
            VendorContact.objects.create(vendor=vendor, **c)
        for a in addresses_data:
            VendorAddress.objects.create(vendor=vendor, **a)
        for b in bank_details_data:
            VendorBankDetail.objects.create(vendor=vendor, **b)

        return vendor

    @transaction.atomic
    def update(self, instance, validated_data):
        # Pop nested data; None means key was absent (don't touch), [] means clear all
        contacts_data     = validated_data.pop('contacts', None)
        addresses_data    = validated_data.pop('addresses', None)
        bank_details_data = validated_data.pop('bank_details', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if contacts_data is not None:
            instance.contacts.all().delete()
            for c in contacts_data:
                VendorContact.objects.create(vendor=instance, **c)

        if addresses_data is not None:
            instance.addresses.all().delete()
            for a in addresses_data:
                VendorAddress.objects.create(vendor=instance, **a)

        if bank_details_data is not None:
            instance.bank_details.all().delete()
            for b in bank_details_data:
                VendorBankDetail.objects.create(vendor=instance, **b)

        return instance


# ─────────────────────────────────────────────────────────────────────────────
# Approved Vendor List
# ─────────────────────────────────────────────────────────────────────────────

class ApprovedVendorListSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    vendor_code = serializers.CharField(source='vendor.vendor_code', read_only=True)
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = ApprovedVendorList
        fields = '__all__'
        read_only_fields = ('tenant', 'approved_by', 'approved_at')

    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return obj.approved_by.get_full_name() or obj.approved_by.username
        return None

    def validate(self, attrs):
        # Prevent adding a blacklisted vendor to AVL
        vendor = attrs.get('vendor')
        if vendor and vendor.status == 'BLACKLISTED':
            raise serializers.ValidationError(
                {"vendor": "Blacklisted vendors cannot be added to the Approved Vendor List."}
            )
        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            validated_data['approved_by'] = request.user
            validated_data['approved_at'] = timezone.now()
        return super().create(validated_data)