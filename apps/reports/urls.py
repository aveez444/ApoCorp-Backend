from rest_framework.routers import DefaultRouter
from .views import VisitReportViewSet

router = DefaultRouter()
router.register(r'visit-reports', VisitReportViewSet, basename='visit-report')

urlpatterns = router.urls