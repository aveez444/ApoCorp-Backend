from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from core.mixins import CustomerLockValidationMixin
from apps.customers.serializers import CustomerReadSerializer
from .models import (
    OrderAcknowledgement,
    OALineItem,
    OACommercialTerms,
    Order
)


class OALineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OALineItem
        exclude = ('oa',)


class OACommercialTermsSerializer(serializers.ModelSerializer):
    class Meta:
        model = OACommercialTerms
        exclude = ('oa',)


class OrderAcknowledgementSerializer(
    CustomerLockValidationMixin,
    serializers.ModelSerializer
):
    line_items = OALineItemSerializer(many=True)
    commercial_terms = OACommercialTermsSerializer(required=False)

    # ── Live customer/enquiry data via FK chain ──
    customer_detail = CustomerReadSerializer(
        source='quotation.enquiry.customer', read_only=True
    )
    enquiry_number = serializers.CharField(
        source='quotation.enquiry.enquiry_number', read_only=True
    )
    quotation_number = serializers.CharField(
        source='quotation.quotation_number', read_only=True
    )
    assigned_to_id = serializers.IntegerField(
        source='quotation.enquiry.assigned_to.id', read_only=True
    )

    class Meta:
        model = OrderAcknowledgement
        fields = "__all__"
        read_only_fields = (
            "tenant",
            "total_value",   # Always recalculated server-side
            "currency",
            "exchange_rate",
            "cancelled_at",
            "oa_number",
            # NOTE: status is NOT read-only — frontend sends DRAFT when saving
        )

    def validate(self, attrs):
        quotation = attrs.get("quotation")
        if quotation and quotation.review_status != "APPROVED":
            raise serializers.ValidationError(
                "Quotation must be approved before creating OA."
            )
        if quotation and quotation.enquiry.customer:
            self.validate_customer_not_locked(quotation.enquiry.customer)
        return attrs

    def _calculate_totals(self, line_items_data):
        """
        Calculate sub_total (excl tax), total_tax, and grand_total
        from a list of line item dicts. Returns (sub_total, total_tax, grand_total).
        """
        sub_total = 0
        total_tax = 0
        for item in line_items_data:
            qty = float(item.get('quantity') or 0)
            price = float(item.get('unit_price') or 0)
            tax_pct = float(item.get('tax_percent') or 0)
            line_excl = qty * price
            line_tax = line_excl * (tax_pct / 100)
            sub_total += line_excl
            total_tax += line_tax
        return sub_total, total_tax, sub_total + total_tax

    def _enrich_line_items(self, line_items_data):
        """
        Recalculate tax_amount and total on each line item dict in-place.
        Returns the enriched list.
        """
        enriched = []
        for item in line_items_data:
            item = dict(item)
            qty = float(item.get('quantity') or 0)
            price = float(item.get('unit_price') or 0)
            tax_pct = float(item.get('tax_percent') or 0)
            line_excl = qty * price
            line_tax = round(line_excl * (tax_pct / 100), 2)
            item['tax_amount'] = line_tax
            item['total'] = round(line_excl + line_tax, 2)
            enriched.append(item)
        return enriched

    @transaction.atomic
    def create(self, validated_data):
        line_items_data = validated_data.pop("line_items")
        commercial_terms_data = validated_data.pop("commercial_terms", None)

        quotation = validated_data["quotation"]

        # Copy currency/exchange_rate from quotation
        validated_data["currency"] = quotation.currency
        validated_data["exchange_rate"] = quotation.exchange_rate

        # Enrich line items and calculate total_value from them
        enriched_items = self._enrich_line_items(line_items_data)
        _, _, grand_total = self._calculate_totals(enriched_items)
        validated_data["total_value"] = grand_total

        # Status defaults to PENDING (new OA from Generate OA button)
        # Allow override if explicitly sent (e.g. DRAFT)
        if "status" not in validated_data:
            validated_data["status"] = "PENDING"

        # Let the model auto-generate oa_number
        validated_data.pop("oa_number", None)

        oa = OrderAcknowledgement.objects.create(**validated_data)

        for item in enriched_items:
            OALineItem.objects.create(oa=oa, **item)

        if commercial_terms_data:
            OACommercialTerms.objects.create(oa=oa, **commercial_terms_data)

        return oa

    @transaction.atomic
    def update(self, instance, validated_data):
        line_items_data = validated_data.pop("line_items", None)
        commercial_terms_data = validated_data.pop("commercial_terms", None)

        # Apply scalar field updates
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # Recalculate total_value if line items are being updated
        if line_items_data is not None:
            enriched_items = self._enrich_line_items(line_items_data)
            _, _, grand_total = self._calculate_totals(enriched_items)
            instance.total_value = grand_total

        instance.last_activity_at = timezone.now()
        instance.save()

        if line_items_data is not None:
            instance.line_items.all().delete()
            for item in enriched_items:
                OALineItem.objects.create(oa=instance, **item)

        if commercial_terms_data is not None:
            if hasattr(instance, 'commercial_terms'):
                instance.commercial_terms.delete()
            OACommercialTerms.objects.create(oa=instance, **commercial_terms_data)

        return instance


class OrderSerializer(serializers.ModelSerializer):

    # ── Live context fields ──
    oa_number = serializers.CharField(source='oa.oa_number', read_only=True)
    customer_detail = CustomerReadSerializer(
        source='oa.quotation.enquiry.customer', read_only=True
    )
    enquiry_number = serializers.CharField(
        source='oa.quotation.enquiry.enquiry_number', read_only=True
    )

    class Meta:
        model = Order
        fields = "__all__"
        read_only_fields = ("tenant",)