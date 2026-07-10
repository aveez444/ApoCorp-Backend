# apps/vendors/urls.py

from rest_framework.routers import DefaultRouter
from .views import VendorViewSet, ApprovedVendorListViewSet

router = DefaultRouter()
router.register(r'avl',   ApprovedVendorListViewSet, basename='avl')
router.register(r'',      VendorViewSet,             basename='vendor')
# avl must be registered BEFORE the empty prefix '' so its URL
# resolves before the vendor detail route catches it.

urlpatterns = router.urls