from rest_framework.routers import DefaultRouter
from .views import OrderAcknowledgementViewSet, OrderViewSet

router = DefaultRouter()

router.register(r'oa', OrderAcknowledgementViewSet, basename='oa')
router.register(r'orders', OrderViewSet, basename='orders')

urlpatterns = router.urls