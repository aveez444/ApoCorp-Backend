from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProductViewSet, product_search  # Import the old search view

router = DefaultRouter()
router.register(r'products', ProductViewSet, basename='product')

urlpatterns = [
    path('', include(router.urls)),
    # Keep the old search endpoint for backward compatibility
    path("search/", product_search, name="product-search"),
]
