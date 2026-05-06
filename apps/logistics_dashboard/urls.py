from django.urls import path
from .views import LogisticsDashboardView

urlpatterns = [
    path('', LogisticsDashboardView.as_view(), name='logistics-dashboard'),
]