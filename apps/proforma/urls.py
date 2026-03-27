from rest_framework.routers import DefaultRouter
from .views import ProformaInvoiceViewSet

router = DefaultRouter()
router.register(r'', ProformaInvoiceViewSet, basename='proforma')

urlpatterns = router.urls