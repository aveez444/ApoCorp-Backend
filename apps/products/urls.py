from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProductViewSet, product_search

router = DefaultRouter()
router.register(r'products', ProductViewSet, basename='product')

urlpatterns = [
    path('', include(router.urls)),
    
    # Explicitly add the reference data endpoints
    # These will work alongside the router
    path('categories/', ProductViewSet.as_view({'get': 'categories'}), name='product-categories'),
    path('product_types/', ProductViewSet.as_view({'get': 'product_types'}), name='product-types'),
    path('units/', ProductViewSet.as_view({'get': 'units'}), name='product-units'),
    
    # Keep the old search endpoint for backward compatibility
    path("search/", product_search, name="product-search"),
]