from rest_framework import serializers
from .models import Product


class ProductSearchSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer used by the quotation product-picker.
    Returns exactly what the frontend needs to auto-fill a line item.
    """

    unit_symbol = serializers.CharField(source="unit.symbol", read_only=True, default="")
    unit_name   = serializers.CharField(source="unit.name",   read_only=True, default="")
    category_name = serializers.CharField(source="category.name", read_only=True, default="")

    class Meta:
        model  = Product
        fields = [
            "id",
            "part_no",
            "name",
            "description",
            "hsn_code",
            "unit_symbol",
            "unit_name",
            "category_name",
            "default_sale_price",
            "brand",
            "make",
            "lead_time_days",
        ]