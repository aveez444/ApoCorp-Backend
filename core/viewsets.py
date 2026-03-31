from rest_framework.viewsets import ModelViewSet


class TenantModelViewSet(ModelViewSet):

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(tenant=self.request.tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)
        