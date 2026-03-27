from rest_framework import serializers
from decimal import Decimal
from django.utils import timezone
from core.mixins import CustomerLockValidationMixin
from apps.customers.serializers import CustomerReadSerializer
from .models import ProformaInvoice, ProformaLineItem, ProformaPayment


class ProformaLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProformaLineItem
        exclude = ('proforma',)


class ProformaPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProformaPayment
        exclude = ('proforma',)


class ProformaInvoiceSerializer(CustomerLockValidationMixin, serializers.ModelSerializer):

    line_items = ProformaLineItemSerializer(many=True, read_only=True)
    payments   = ProformaPaymentSerializer(many=True, read_only=True)

    # Live context via FK chain
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    oa_number    = serializers.CharField(source='order.oa.oa_number', read_only=True)
    customer_detail = CustomerReadSerializer(
        source='order.oa.quotation.enquiry.customer', read_only=True
    )
    enquiry_number = serializers.CharField(
        source='order.oa.quotation.enquiry.enquiry_number', read_only=True
    )

    class Meta:
        model = ProformaInvoice
        fields = '__all__'
        read_only_fields = (
            'tenant',
            'proforma_number',
            'status',
            # Always server-calculated — never accepted from client
            'sub_total',
            'total_tax',
            'total_amount',
            'total_paid',
            'total_receivable',
            'ff_amount',
            'discount_amount',
            'advance_amount',
            'net_amount',
            'gst_amount',
            'gst_percentage',
        )

    def validate(self, attrs):
        order = attrs.get('order')
        if order:
            try:
                customer = order.oa.quotation.enquiry.customer
                self.validate_customer_not_locked(customer)
            except AttributeError:
                pass
        # Percentage values must be 0–100
        for field in ('ff_percentage', 'discount_percentage', 'advance_percentage'):
            val = attrs.get(field)
            if val is not None and not (0 <= val <= 100):
                raise serializers.ValidationError({field: 'Must be between 0 and 100.'})
        return attrs

    def _generate_proforma_number(self):
        year   = timezone.now().strftime('%Y')
        month  = timezone.now().strftime('%m')
        prefix = f'PF/{year}/{month}/'
        last   = ProformaInvoice.objects.filter(
            proforma_number__startswith=prefix
        ).order_by('proforma_number').last()
        seq = int(last.proforma_number.split('/')[-1]) + 1 if last else 1
        return f'{prefix}{seq:04d}'

    def _build_line_items_and_totals(self, proforma, oa_line_items):
        """
        Copy OA line items into proforma, compute sub_total / total_tax / total_amount.
        Returns (sub_total, total_tax, total_amount).
        """
        sub_total  = Decimal('0')
        total_tax  = Decimal('0')

        for li in oa_line_items:
            qty      = Decimal(str(li.quantity))
            price    = Decimal(str(li.unit_price))
            tax_pct  = Decimal(str(li.tax_percent)) if li.tax_percent else Decimal('0')
            excl     = qty * price
            line_tax = (excl * tax_pct / 100).quantize(Decimal('0.01'))
            line_tot = excl + line_tax

            sub_total += excl
            total_tax += line_tax

            ProformaLineItem.objects.create(
                proforma         = proforma,
                job_code         = li.job_code         or '',
                customer_part_no = li.customer_part_no or '',
                part_no          = li.part_no          or '',
                description      = li.description      or '',
                hsn_code         = li.hsn_code         or '',
                quantity         = li.quantity,
                unit             = li.unit             or 'NOS',
                unit_price       = li.unit_price,
                tax_group_code   = li.tax_group_code   or '',
                tax_percent      = tax_pct,
                tax_amount       = line_tax,
                total            = line_tot,
            )

        return sub_total, total_tax, sub_total + total_tax

    def create(self, validated_data):
        order = validated_data['order']

        # Guard: prevent duplicate proformas for the same order
        if ProformaInvoice.objects.filter(order=order).exists():
            raise serializers.ValidationError(
                'A proforma invoice already exists for this order.'
            )

        proforma = ProformaInvoice.objects.create(
            **validated_data,
            proforma_number = self._generate_proforma_number(),
            currency        = order.currency     or 'INR',
            exchange_rate   = order.exchange_rate or 1,
            status          = 'DRAFT',
        )

        sub_total, total_tax, total_amount = self._build_line_items_and_totals(
            proforma, order.oa.line_items.all()
        )

        proforma.sub_total    = sub_total
        proforma.total_tax    = total_tax
        proforma.total_amount = total_amount
        proforma.total_paid   = Decimal('0')
        # Legacy aliases
        proforma.net_amount   = sub_total
        proforma.gst_amount   = total_tax

        # Compute deductions + total_receivable
        proforma.recalculate_deductions()
        proforma.save()

        return proforma

    def update(self, instance, validated_data):
        """
        PATCH is only used to update ff_percentage, discount_percentage,
        advance_percentage (and invoice_date if needed).
        All amounts are recalculated server-side.
        Guard: not allowed if status is PAID.
        """
        if instance.status == 'PAID':
            raise serializers.ValidationError(
                'Cannot modify a fully paid proforma invoice.'
            )

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # Recalculate all deduction amounts from updated percentages
        instance.recalculate_deductions()
        instance.save()

        return instance


class ProformaPaymentRecordSerializer(serializers.ModelSerializer):

    class Meta:
        model   = ProformaPayment
        exclude = ('proforma',)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('Payment amount must be positive.')
        return value