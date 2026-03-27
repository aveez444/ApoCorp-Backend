from django.contrib import admin
from .models import Product, ProductCategory, UnitOfMeasure, ProductType


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):

    list_display = ("name", "code", "parent", "is_active", "created_at")
    search_fields = ("name", "code")
    list_filter = ("is_active",)
    ordering = ("name",)


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(admin.ModelAdmin):

    list_display = ("name", "symbol", "is_active", "created_at")
    search_fields = ("name", "symbol")
    list_filter = ("is_active",)


@admin.register(ProductType)
class ProductTypeAdmin(admin.ModelAdmin):

    list_display = ("name", "code", "is_active", "created_at")
    search_fields = ("name", "code")
    list_filter = ("is_active",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):

    list_display = (
        "part_no",
        "name",
        "category",
        "unit",
        "default_sale_price",
        "is_active",
    )

    search_fields = (
        "part_no",
        "name",
        "description",
    )

    list_filter = (
        "category",
        "unit",
        "is_active",
    )

    ordering = ("part_no",)

    autocomplete_fields = ("category", "unit", "product_type")

    readonly_fields = ("created_at", "updated_at")