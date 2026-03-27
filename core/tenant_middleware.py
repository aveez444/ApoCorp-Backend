# core/tenant_middleware.py

from django.http import JsonResponse
from apps.tenants.models import Tenant
import re

class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Define public endpoints that don't need tenant header
        public_paths = [
            '/admin/',
            '/media/',          # Add this line to exclude media files
            '/api/accounts/login/',
            '/api/accounts/token/refresh/',  # if you have refresh endpoint
        ]
        
        # Check if current path is public
        for path in public_paths:
            if request.path.startswith(path):
                return self.get_response(request)
        
        # For all other endpoints, require tenant header
        tenant_id = request.headers.get('X-Tenant-ID')
        
        if not tenant_id:
            return JsonResponse(
                {"error": "X-Tenant-ID header missing"},
                status=400
            )

        try:
            tenant = Tenant.objects.get(
                id=tenant_id,
                is_active=True
            )
            request.tenant = tenant
        except Tenant.DoesNotExist:
            return JsonResponse(
                {"error": "Invalid or inactive tenant"},
                status=404
            )

        return self.get_response(request)