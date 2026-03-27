from django.urls import path

from .views import (
    TenantLoginView,
    PermissionListView,
    AssignPermissionView,
    RemovePermissionView,
    LogoutView,
    TenantEmployeeListView,
    UserPermissionListView,
    UsersListView,
)

urlpatterns = [
    path('login/', TenantLoginView.as_view()),
    path('permissions/', PermissionListView.as_view()),
    path('assign-permission/', AssignPermissionView.as_view()),
    path('remove-permission/', RemovePermissionView.as_view()),
    path("user-permissions/<int:user_id>/", UserPermissionListView.as_view()),
    path('logout/', LogoutView.as_view()),
    path("tenant/employees/", TenantEmployeeListView.as_view()),
    # Used by SendNotification to fetch recipients; supports ?role=employee
    path("users/", UsersListView.as_view()),
]