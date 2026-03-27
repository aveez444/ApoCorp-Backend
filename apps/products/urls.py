from django.urls import path
from .views import product_search

urlpatterns = [
    path("search/", product_search, name="product-search"),
]