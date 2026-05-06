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
    ForgotPasswordView,
    ValidateResetTokenView,
    ResetPasswordView,
    ChangePasswordView,
)

urlpatterns = [
    path('login/', TenantLoginView.as_view()),
    path('permissions/', PermissionListView.as_view()),
    path('assign-permission/', AssignPermissionView.as_view()),
    path('remove-permission/', RemovePermissionView.as_view()),
    path("user-permissions/<int:user_id>/", UserPermissionListView.as_view()),
    path('logout/', LogoutView.as_view()),
    path("tenant/employees/", TenantEmployeeListView.as_view()),
    path("users/", UsersListView.as_view()),
    
    # Password reset endpoints
    path('forgot-password/', ForgotPasswordView.as_view(), name='forgot-password'),
    path('validate-reset-token/', ValidateResetTokenView.as_view(), name='validate-reset-token'),
    path('reset-password/', ResetPasswordView.as_view(), name='reset-password'),
    path('change-password/', ChangePasswordView.as_view(), name='change-password'),
]