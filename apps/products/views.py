from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Q
from .models import Product
from .serializers import ProductSearchSerializer


@api_view(["GET"])
def product_search(request):
    """
    Efficient product search for the quotation product picker.

    Query params:
      q        — search term (matches part_no OR name, case-insensitive)
      page     — 1-based page number (default 1)
      limit    — items per page, max 50 (default 20)
      category — UUID of ProductCategory to filter by (optional)
      active   — "true" / "false", default "true"

    Returns:
      { results: [...], total: N, page: N, pages: N, has_next: bool }
    """

    query    = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    active   = request.GET.get("active", "true").lower() != "false"

    try:
        limit = min(int(request.GET.get("limit", 20)), 50)
    except ValueError:
        limit = 20

    try:
        page = max(int(request.GET.get("page", 1)), 1)
    except ValueError:
        page = 1

    # Base queryset — tenant-scoped always
    qs = Product.objects.filter(tenant=request.tenant)

    if active:
        qs = qs.filter(is_active=True)

    if category:
        qs = qs.filter(category_id=category)

    # Full-text style search: split query into tokens so "swas sys" matches "SWAS System"
    if query:
        tokens = query.split()
        for token in tokens:
            qs = qs.filter(
                Q(part_no__icontains=token) |
                Q(name__icontains=token)
            )

    qs = qs.select_related("unit").order_by("part_no")

    total  = qs.count()
    offset = (page - 1) * limit
    items  = qs[offset: offset + limit]

    pages = max((total + limit - 1) // limit, 1)

    serializer = ProductSearchSerializer(items, many=True)

    return Response({
        "results":  serializer.data,
        "total":    total,
        "page":     page,
        "pages":    pages,
        "has_next": page < pages,
    })