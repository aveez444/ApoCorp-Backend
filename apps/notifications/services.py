# apps/notifications/services.py - Fixed to match your models

from django.utils import timezone
from .models import Notification, NotificationRecipient


def create_notification(user, title, message, link='', notification_type='INFO', 
                        created_by=None, is_broadcast=False, recipient_ids=None):
    """
    Create a notification for a user or multiple users.
    
    Args:
        user: Single user (if not broadcast)
        title: Notification title
        message: Notification message
        link: Frontend route to redirect when clicked
        notification_type: INFO, WARNING, SUCCESS, ALERT
        created_by: User creating the notification
        is_broadcast: If True, send to all employees
        recipient_ids: List of user IDs (if not broadcast)
    
    Returns:
        Notification instance
    """
    if not user and not is_broadcast and not recipient_ids:
        return None
    
    # Create the notification
    notification = Notification.objects.create(
        title=title,
        message=message,
        type=notification_type.upper() if notification_type.upper() in ['INFO', 'WARNING', 'SUCCESS', 'ALERT'] else 'INFO',
        link=link or '',
        created_by=created_by,
        is_broadcast=is_broadcast,
        tenant=user.tenant if user else None,  # If user has tenant
    )
    
    # Create recipients
    if is_broadcast:
        # Send to all employees in the tenant
        from apps.accounts.models import TenantUser
        employees = TenantUser.objects.filter(
            tenant=notification.tenant,
            role='employee',
            is_active=True
        ).values_list('user', flat=True)
        
        NotificationRecipient.objects.bulk_create([
            NotificationRecipient(notification=notification, user_id=uid)
            for uid in employees
        ], ignore_conflicts=True)
    
    elif recipient_ids:
        # Send to specific users
        NotificationRecipient.objects.bulk_create([
            NotificationRecipient(notification=notification, user_id=uid)
            for uid in recipient_ids
        ], ignore_conflicts=True)
    
    elif user:
        # Send to single user
        NotificationRecipient.objects.create(
            notification=notification,
            user=user
        )
    
    return notification


def create_engineering_notification(user, title, message, link='', package=None, ecn=None):
    """
    Convenience function for engineering package notifications.
    """
    return create_notification(
        user=user,
        title=title,
        message=message,
        link=link,
        notification_type='INFO',
        created_by=user if user else None,
    )


def notify_engineering_release(package, project_manager):
    """
    Notify PM when engineering releases a package.
    """
    return create_notification(
        user=project_manager,
        title=f"📦 Engineering Package Released: {package.package_number}",
        message=f"Engineering has released {package.version} for project {package.project.name}",
        link=f"/projects/{package.project.id}/engineering-packages/{package.id}",
        notification_type='INFO',
        created_by=package.released_by,
    )


def notify_acceptance(package, engineering_team):
    """
    Notify engineering when PM accepts a package.
    """
    # Get engineering team users (adjust based on your role system)
    from apps.accounts.models import TenantUser
    engineering_users = TenantUser.objects.filter(
        tenant=package.project.tenant,
        role__in=['engineering', 'manager'],
        is_active=True
    ).values_list('user_id', flat=True)
    
    return create_notification(
        user=None,
        title=f"✅ Package Accepted: {package.package_number}",
        message=f"{package.project.name} has accepted {package.version}",
        link=f"/engineering/packages/{package.id}",
        notification_type='SUCCESS',
        created_by=package.accepted_by,
        recipient_ids=list(engineering_users),
    )


def notify_ecn(ecn, project_manager):
    """
    Notify PM when engineering creates a change notice.
    """
    return create_notification(
        user=project_manager,
        title=f"🔄 Engineering Change Notice: {ecn.ecn_number}",
        message=f"Engineering proposes revision {ecn.new_version} for {ecn.package.project.name}",
        link=f"/projects/{ecn.package.project.id}/ecn/{ecn.id}",
        notification_type='WARNING',
        created_by=ecn.requested_by,
    )


def notify_ecn_decision(ecn, decision, engineering_team):
    """
    Notify engineering about ECN decision.
    """
    from apps.accounts.models import TenantUser
    engineering_users = TenantUser.objects.filter(
        tenant=ecn.package.project.tenant,
        role__in=['engineering', 'manager'],
        is_active=True
    ).values_list('user_id', flat=True)
    
    status_emoji = {
        'APPROVED': '✅',
        'REJECTED': '❌',
        'DEFERRED': '⏳',
    }
    
    return create_notification(
        user=None,
        title=f"{status_emoji.get(decision, '📋')} ECN {ecn.ecn_number}: {decision}",
        message=f"Project {ecn.package.project.name} has {decision.lower()} the change notice",
        link=f"/engineering/ecn/{ecn.id}",
        notification_type='INFO',
        created_by=ecn.reviewed_by,
        recipient_ids=list(engineering_users),
    )