# apps/engineering/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'items', views.EngineeringItemViewSet, basename='engineering-item')
router.register(r'boms', views.EngineeringBOMViewSet, basename='engineering-bom')
router.register(r'documents', views.EngineeringDocumentViewSet, basename='engineering-document')

urlpatterns = [
    path('', include(router.urls)),
]