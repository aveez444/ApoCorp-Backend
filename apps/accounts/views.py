from rest_framework.views import APIView
from rest_framework.response import Response
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from .models import TenantUser
from apps.tenants.models import Tenant

class TenantLoginView(APIView):

    permission_classes = []

    def post(self, request):

        company_code = request.data.get("company_code")  # subdomain
        username = request.data.get("username")
        password = request.data.get("password")

        if not company_code or not username or not password:
            return Response(
                {"error": "Company code, username and password required"},
                status=400
            )

        try:
            tenant = Tenant.objects.get(
                subdomain=company_code,
                is_active=True
            )
        except Tenant.DoesNotExist:
            return Response(
                {"error": "Invalid company code"},
                status=400
            )

        user = authenticate(username=username, password=password)

        if not user:
            return Response(
                {"error": "Invalid credentials"},
                status=400
            )

        tenant_user = TenantUser.objects.filter(
            user=user,
            tenant=tenant,
            is_active=True
        ).first()

        if not tenant_user:
            return Response(
                {"error": "User not allowed for this company"},
                status=403
            )

        refresh = RefreshToken.for_user(user)

        return Response({
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "tenant_id": str(tenant.id),
            "role": tenant_user.role
        })


from django.contrib.auth.models import Permission
from rest_framework.permissions import IsAuthenticated
from core.permissions import IsManager
from rest_framework.views import APIView
from rest_framework.response import Response


class PermissionListView(APIView):
    permission_classes = [IsAuthenticated, IsManager]

    def get(self, request):

        allowed_apps = [
            "customers",
            "enquiries",
            "quotations",
            "oa",
        ]

        permissions = Permission.objects.filter(
            content_type__app_label__in=allowed_apps
        ).values(
            "id",
            "codename",
            "name",
            "content_type__app_label"
        )

        return Response(list(permissions))
    

from django.contrib.auth.models import User, Permission
from apps.accounts.models import TenantUser


class AssignPermissionView(APIView):
    permission_classes = [IsAuthenticated, IsManager]

    def post(self, request):

        user_id = request.data.get("user_id")
        permission_id = request.data.get("permission_id")

        try:
            user = User.objects.get(id=user_id)
            permission = Permission.objects.get(id=permission_id)
        except (User.DoesNotExist, Permission.DoesNotExist):
            return Response({"error": "Invalid user or permission"}, status=400)

        # 🔐 Tenant safety check
        tenant_user = TenantUser.objects.filter(
            user=user,
            tenant=request.tenant,
            is_active=True
        ).first()

        if not tenant_user:
            return Response(
                {"error": "User not part of this tenant"},
                status=403
            )

        # 🔐 Allow only CRM app permissions
        allowed_apps = ["customers", "enquiries", "quotations", "oa"]

        if permission.content_type.app_label not in allowed_apps:
            return Response(
                {"error": "Permission not allowed"},
                status=403
            )

        user.user_permissions.add(permission)

        return Response({"message": "Permission assigned successfully"})
    
class RemovePermissionView(APIView):
    permission_classes = [IsAuthenticated, IsManager]

    def post(self, request):

        user_id = request.data.get("user_id")
        permission_id = request.data.get("permission_id")

        try:
            user = User.objects.get(id=user_id)
            permission = Permission.objects.get(id=permission_id)
        except (User.DoesNotExist, Permission.DoesNotExist):
            return Response({"error": "Invalid user or permission"}, status=400)

        tenant_user = TenantUser.objects.filter(
            user=user,
            tenant=request.tenant,
            is_active=True
        ).first()

        if not tenant_user:
            return Response(
                {"error": "User not part of this tenant"},
                status=403
            )

        user.user_permissions.remove(permission)

        return Response({"message": "Permission removed successfully"})

class UserPermissionListView(APIView):
    permission_classes = [IsAuthenticated, IsManager]

    def get(self, request, user_id):

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

        tenant_user = TenantUser.objects.filter(
            user=user,
            tenant=request.tenant
        ).first()

        if not tenant_user:
            return Response(
                {"error": "User not part of this tenant"},
                status=403
            )

        permissions = user.user_permissions.values(
            "id",
            "codename",
            "name",
            "content_type__app_label"
        )

        return Response(list(permissions))
    
class CreateTenantWithManagerView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):

        if not request.user.is_superuser:
            return Response({"error": "Only superadmin allowed"}, status=403)

        company_name = request.data.get("company_name")
        subdomain = request.data.get("subdomain")

        manager_username = request.data.get("manager_username")
        manager_password = request.data.get("manager_password")

        if Tenant.objects.filter(subdomain=subdomain).exists():
            return Response(
                {"error": "Subdomain already exists"},
                status=400
            )

        tenant = Tenant.objects.create(
            company_name=company_name,
            subdomain=subdomain
        )

        user = User.objects.create_user(
            username=manager_username,
            password=manager_password
        )

        TenantUser.objects.create(
            user=user,
            tenant=tenant,
            role="manager"
        )

        return Response({"message": "Tenant and manager created"})


class CreateEmployeeView(APIView):
    permission_classes = [IsAuthenticated, IsManager]

    def post(self, request):

        username = request.data.get("username")
        password = request.data.get("password")

        if User.objects.filter(username=username).exists():
            return Response(
                {"error": "Username already exists"},
                status=400
            )

        user = User.objects.create_user(
            username=username,
            password=password
        )

        TenantUser.objects.create(
            user=user,
            tenant=request.tenant,
            role="employee"
        )

        return Response({"message": "Employee created"})


from rest_framework.permissions import IsAuthenticated
from rest_framework import status


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")

            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()

            return Response(
                {"message": "Logged out successfully"},
                status=status.HTTP_200_OK
            )

        except Exception:
            return Response(
                {"error": "Invalid token"},
                status=status.HTTP_400_BAD_REQUEST
            )


from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from apps.accounts.models import TenantUser


class TenantEmployeeListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):

        tenant_user = TenantUser.objects.filter(
            user=request.user,
            tenant=request.tenant
        ).first()

        if not tenant_user or tenant_user.role != "manager":
            raise PermissionDenied("Only manager allowed")

        employees = TenantUser.objects.filter(
            tenant=request.tenant,
            role="employee",
            is_active=True
        ).select_related("user")

        data = [
            {
                "id": emp.user.id,
                "username": emp.user.username,
                "role": emp.role
            }
            for emp in employees
        ]

        return Response(data)


class UsersListView(APIView):
    """
    GET /api/accounts/users/
    GET /api/accounts/users/?role=employee
    GET /api/accounts/users/?role=manager
    GET /api/accounts/users/?role=all   ← returns everyone; used for regional_manager dropdown

    Returns active tenant users, optionally filtered by role.
    Both managers and employees can call this so that the enquiry form
    can populate the regional_manager dropdown for any logged-in user.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):

        # Verify caller belongs to this tenant
        caller = TenantUser.objects.filter(
            user=request.user,
            tenant=request.tenant,
            is_active=True,
        ).first()

        if not caller:
            raise PermissionDenied("Not a member of this tenant.")

        role_filter = request.query_params.get("role")

        qs = TenantUser.objects.filter(
            tenant=request.tenant,
            is_active=True,
        ).select_related("user")

        # ?role=all  → return everyone (employees + managers)
        # ?role=<x>  → filter by that specific role
        # no param   → managers only (preserves original behaviour)
        if role_filter == "all":
            pass  # no extra filter needed
        elif role_filter:
            qs = qs.filter(role=role_filter)
        else:
            # Original behaviour: only managers can list without a role filter
            if caller.role != "manager":
                raise PermissionDenied("Only managers can list users.")

        data = [
            {
                "id": tu.user.id,
                "username": tu.user.username,
                "first_name": tu.user.first_name,
                "last_name": tu.user.last_name,
                "email": tu.user.email,
                "role": tu.role,
            }
            for tu in qs
        ]

        return Response(data)
    
# Add these imports at the top
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from .models import PasswordResetToken
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

class ForgotPasswordView(APIView):
    """
    Step 1: User provides company_code and username, system sends reset link to registered email
    """
    permission_classes = [AllowAny]

    def post(self, request):
        company_code = request.data.get('company_code')
        username = request.data.get('username')
        
        if not company_code or not username:
            return Response(
                {"error": "Company code and username are required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # First validate tenant exists
        try:
            tenant = Tenant.objects.get(
                subdomain=company_code,
                is_active=True
            )
        except Tenant.DoesNotExist:
            # Don't reveal that tenant doesn't exist for security
            return Response(
                {"message": "If the credentials are valid, you will receive a password reset link."},
                status=status.HTTP_200_OK
            )

        # Check if user exists and belongs to this tenant
        try:
            user = User.objects.get(username=username)
            tenant_user = TenantUser.objects.filter(
                user=user,
                tenant=tenant,
                is_active=True
            ).first()
            
            if not tenant_user:
                return Response(
                    {"message": "If the credentials are valid, you will receive a password reset link."},
                    status=status.HTTP_200_OK
                )
                
        except User.DoesNotExist:
            return Response(
                {"message": "If the credentials are valid, you will receive a password reset link."},
                status=status.HTTP_200_OK
            )

        # Check if user has an email registered
        if not user.email:
            return Response(
                {"error": "No email address registered for this user. Please contact your administrator."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Delete any existing unused tokens for this user
        PasswordResetToken.objects.filter(
            user=user, 
            is_used=False,
            expires_at__gt=timezone.now()
        ).delete()

        # Create new token
        reset_token = PasswordResetToken.objects.create(user=user)

        # Build reset URL with tenant context
        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={reset_token.token}&company_code={company_code}"

        # Email content
        context = {
            'user': user,
            'reset_url': reset_url,
            'company_name': tenant.company_name,
            'expiry_hours': 1,
            'username': user.username,
        }

        html_message = render_to_string('accounts/password_reset_email.html', context)
        plain_message = strip_tags(html_message)

        try:
            send_mail(
                subject=f'Password Reset Request - {tenant.company_name}',
                message=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_message,
                fail_silently=False,
            )
        except Exception as e:
            print(f"Email send error: {e}")
            return Response(
                {"error": "Unable to send email. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response(
            {"message": f"Password reset link has been sent to {user.email}"},
            status=status.HTTP_200_OK
        )

class ValidateResetTokenView(APIView):
    """
    Step 2: Validate the reset token before showing reset form
    """
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        company_code = request.data.get('company_code')
        
        if not token:
            return Response(
                {"error": "Token is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            reset_token = PasswordResetToken.objects.get(token=token)
        except PasswordResetToken.DoesNotExist:
            return Response(
                {"error": "Invalid or expired reset link. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not reset_token.is_valid():
            return Response(
                {"error": "This reset link has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Verify the user belongs to the company code
        if company_code:
            try:
                tenant = Tenant.objects.get(subdomain=company_code, is_active=True)
                tenant_user = TenantUser.objects.filter(
                    user=reset_token.user,
                    tenant=tenant,
                    is_active=True
                ).first()
                
                if not tenant_user:
                    return Response(
                        {"error": "Invalid reset link. User not associated with this company."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Tenant.DoesNotExist:
                return Response(
                    {"error": "Invalid company code"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        return Response(
            {
                "message": "Token is valid", 
                "email": reset_token.user.email,
                "username": reset_token.user.username
            },
            status=status.HTTP_200_OK
        )

class ResetPasswordView(APIView):
    """
    Step 3: User submits new password with token
    """
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        company_code = request.data.get('company_code')
        new_password = request.data.get('new_password')
        confirm_password = request.data.get('confirm_password')

        # Validation
        if not token or not new_password or not confirm_password:
            return Response(
                {"error": "Token, new password and confirm password are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if new_password != confirm_password:
            return Response(
                {"error": "Passwords do not match"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(new_password) < 8:
            return Response(
                {"error": "Password must be at least 8 characters long"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            reset_token = PasswordResetToken.objects.get(token=token)
        except PasswordResetToken.DoesNotExist:
            return Response(
                {"error": "Invalid or expired reset link"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not reset_token.is_valid():
            return Response(
                {"error": "This reset link has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Verify company code if provided
        if company_code:
            try:
                tenant = Tenant.objects.get(subdomain=company_code, is_active=True)
                tenant_user = TenantUser.objects.filter(
                    user=reset_token.user,
                    tenant=tenant,
                    is_active=True
                ).first()
                
                if not tenant_user:
                    return Response(
                        {"error": "Invalid reset link. User not associated with this company."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Tenant.DoesNotExist:
                return Response(
                    {"error": "Invalid company code"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Reset the password
        user = reset_token.user
        user.set_password(new_password)
        user.save()

        # Mark token as used
        reset_token.is_used = True
        reset_token.save()

        # Invalidate all existing JWT tokens for this user
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
        OutstandingToken.objects.filter(user=user).delete()

        return Response(
            {"message": "Password has been reset successfully. You can now login with your new password."},
            status=status.HTTP_200_OK
        )


class ChangePasswordView(APIView):
    """
    For authenticated users to change their password
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        current_password = request.data.get('current_password')
        new_password = request.data.get('new_password')
        confirm_password = request.data.get('confirm_password')

        if not current_password or not new_password or not confirm_password:
            return Response(
                {"error": "Current password, new password and confirm password are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if new_password != confirm_password:
            return Response(
                {"error": "New passwords do not match"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(new_password) < 8:
            return Response(
                {"error": "Password must be at least 8 characters long"},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user
        
        # Verify current password
        if not user.check_password(current_password):
            return Response(
                {"error": "Current password is incorrect"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Set new password
        user.set_password(new_password)
        user.save()

        # Invalidate all existing tokens
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
        OutstandingToken.objects.filter(user=user).delete()

        return Response(
            {"message": "Password changed successfully. Please login again."},
            status=status.HTTP_200_OK
        )