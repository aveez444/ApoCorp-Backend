from rest_framework.routers import DefaultRouter
from .views import EnquiryViewSet

router = DefaultRouter()
router.register(r'', EnquiryViewSet, basename='enquiry')

urlpatterns = router.urls