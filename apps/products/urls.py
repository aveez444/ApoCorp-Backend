from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProductViewSet, product_search

router = DefaultRouter()
router.register(r'', ProductViewSet, basename='product')

urlpatterns = [
    # must come before the router include so it isn't swallowed by
    # the generic /products/<pk>/ pattern
    path('search/', product_search, name='product-search'),
    path('', include(router.urls)),
]