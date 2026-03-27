# customers/serializers.py
from rest_framework import serializers
from .models import Customer, CustomerAddress, CustomerPOC


class CustomerAddressInputSerializer(serializers.ModelSerializer):
    """
    Used for CREATE and UPDATE writes.
    address_type is always set programmatically by the parent serializer,
    so we exclude it here to avoid the "This field is required" validation error.
    """
    class Meta:
        model = CustomerAddress
        exclude = ("customer", "address_type")


class CustomerAddressSerializer(serializers.ModelSerializer):
    """
    Used for READ responses — includes address_type so the frontend
    can distinguish BILLING vs SHIPPING.
    """
    class Meta:
        model = CustomerAddress
        exclude = ("customer",)


class CustomerPOCSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerPOC
        exclude = ("customer",)


# ── Lightweight read serializer used by Enquiry, Quotation, OA etc. ──
class CustomerReadSerializer(serializers.ModelSerializer):
    pocs = CustomerPOCSerializer(many=True, read_only=True)
    addresses = CustomerAddressSerializer(many=True, read_only=True)
    
    class Meta:
        model = Customer
        fields = (
            "id", 
            "customer_code", 
            "company_name", 
            "tier",
            "region", 
            "country", 
            "state", 
            "city",
            "telephone_primary",
            "telephone_secondary",
            "email",
            "pan_number", 
            "gst_number",
            "default_currency",
            "pocs", 
            "addresses",
        )


class CustomerDropdownSerializer(serializers.ModelSerializer):
    """Ultra-lightweight serializer for dropdowns"""
    
    class Meta:
        model = Customer
        fields = ("id", "customer_code", "company_name", "email", "telephone_primary")


class CustomerSerializer(serializers.ModelSerializer):

    # Write: frontend sends billing_address / shipping_address without address_type
    billing_address  = CustomerAddressInputSerializer(required=False, write_only=True)
    shipping_address = CustomerAddressInputSerializer(required=False, write_only=True)

    # Read/Write: POCs
    pocs = CustomerPOCSerializer(many=True, required=False)

    # Read only: full address list with address_type included
    addresses = CustomerAddressSerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = "__all__"
        read_only_fields = (
            "tenant",
            "customer_code",
            "locked_at",
            "locked_by",
            "created_at",
            "updated_at",
        )

    def create(self, validated_data):
        billing_data  = validated_data.pop("billing_address", None)
        shipping_data = validated_data.pop("shipping_address", None)
        pocs_data     = validated_data.pop("pocs", [])

        customer = Customer.objects.create(**validated_data)

        if billing_data:
            CustomerAddress.objects.create(
                customer=customer,
                address_type="BILLING",
                **billing_data
            )
        if shipping_data:
            CustomerAddress.objects.create(
                customer=customer,
                address_type="SHIPPING",
                **shipping_data
            )
        for poc in pocs_data:
            CustomerPOC.objects.create(customer=customer, **poc)

        return customer

    def update(self, instance, validated_data):
        billing_data  = validated_data.pop("billing_address", None)
        shipping_data = validated_data.pop("shipping_address", None)
        pocs_data     = validated_data.pop("pocs", None)

        # Update scalar fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Update billing address: update existing or create new
        if billing_data is not None:
            billing_addr = instance.addresses.filter(address_type="BILLING").first()
            if billing_addr:
                for attr, value in billing_data.items():
                    setattr(billing_addr, attr, value)
                billing_addr.save()
            else:
                CustomerAddress.objects.create(
                    customer=instance,
                    address_type="BILLING",
                    **billing_data
                )

        # Update shipping address: update existing or create new
        if shipping_data is not None:
            shipping_addr = instance.addresses.filter(address_type="SHIPPING").first()
            if shipping_addr:
                for attr, value in shipping_data.items():
                    setattr(shipping_addr, attr, value)
                shipping_addr.save()
            else:
                CustomerAddress.objects.create(
                    customer=instance,
                    address_type="SHIPPING",
                    **shipping_data
                )

        # Replace POCs wholesale if provided
        if pocs_data is not None:
            instance.pocs.all().delete()
            for poc in pocs_data:
                CustomerPOC.objects.create(customer=instance, **poc)

        return instance