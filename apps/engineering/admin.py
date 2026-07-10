# apps/engineering/admin.py
from django.contrib import admin
from .models import (
    EngineeringItemMaster,
    EngineeringItemRevision,
    EngineeringDocument,
    BOMLine,
    EngineeringBOM
)


class EngineeringItemRevisionInline(admin.TabularInline):
    model = EngineeringItemRevision
    extra = 0
    fields = ('revision', 'is_current', 'effective_date', 'obsoleted_date')
    readonly_fields = ('created_at',)


class EngineeringDocumentInline(admin.TabularInline):
    model = EngineeringDocument
    extra = 0
    fields = ('doc_type', 'title', 'file', 'uploaded_at')
    readonly_fields = ('uploaded_at',)


@admin.register(EngineeringItemMaster)
class EngineeringItemMasterAdmin(admin.ModelAdmin):
    list_display = ('item_code', 'name', 'item_class', 'category', 'current_revision', 'is_active')
    list_filter = ('item_class', 'category', 'is_active', 'tenant')
    search_fields = ('item_code', 'name', 'drawing_number', 'manufacturer_part_number')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [EngineeringItemRevisionInline, EngineeringDocumentInline]
    fieldsets = (
        ('Basic Info', {
            'fields': ('tenant', 'item_code', 'name', 'description', 'uom')
        }),
        ('Classification', {
            'fields': ('item_class', 'category', 'sub_category')
        }),
        ('Engineering', {
            'fields': ('drawing_number', 'current_revision', 'specification')
        }),
        ('Vendor Info', {
            'fields': ('make', 'model', 'manufacturer_part_number', 'customer_part_number')
        }),
        ('Inventory Link', {
            'fields': ('inventory_item',)
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Audit', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(EngineeringItemRevision)
class EngineeringItemRevisionAdmin(admin.ModelAdmin):
    list_display = ('item', 'revision', 'is_current', 'effective_date', 'obsoleted_date')
    list_filter = ('is_current', 'effective_date')
    search_fields = ('item__item_code', 'item__name', 'revision')
    readonly_fields = ('created_at',)


@admin.register(EngineeringDocument)
class EngineeringDocumentAdmin(admin.ModelAdmin):
    list_display = ('item', 'title', 'doc_type', 'uploaded_at')
    list_filter = ('doc_type',)
    search_fields = ('item__item_code', 'item__name', 'title')
    readonly_fields = ('uploaded_at',)

# apps/engineering/admin.py - Add BOM admin

@admin.register(EngineeringBOM)
class EngineeringBOMAdmin(admin.ModelAdmin):
    list_display = ('bom_number', 'name', 'parent_item', 'version', 'status', 'is_active')
    list_filter = ('status', 'is_active', 'created_at')
    search_fields = ('bom_number', 'name', 'parent_item__item_code', 'parent_item__name')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Basic Info', {
            'fields': ('tenant', 'bom_number', 'name', 'parent_item', 'version')
        }),
        ('Dates', {
            'fields': ('effective_date', 'obsolete_date')
        }),
        ('Status', {
            'fields': ('status', 'is_active', 'approved_by', 'approved_at')
        }),
        ('Description', {
            'fields': ('description',)
        }),
        ('Audit', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


class BOMLineInline(admin.TabularInline):
    model = BOMLine
    extra = 1
    fields = ('item', 'quantity', 'uom', 'item_class', 'is_phantom', 'sort_order')
    raw_id_fields = ('item',)