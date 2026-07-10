# apps/projects/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'', views.ProjectViewSet, basename='project')
# router.register(r'cost-entries', views.ProjectCostEntryViewSet, basename='project-cost-entry')
# router.register(r'milestones', views.ProjectMilestoneViewSet, basename='project-milestone')
# router.register(r'documents', views.ProjectDocumentViewSet, basename='project-document')

urlpatterns = [
    path('', include(router.urls)),
]