# apps/engineering/services.py
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from django.contrib.auth.models import User


def explode_bom(bom, qty_multiplier=Decimal('1'), depth=0, parent_path=''):
    """Recursively explode a BOM into flat list of all leaf-level items."""
    from .models import BOMLine
    
    result = []
    top_lines = bom.lines.filter(parent_line__isnull=True).order_by('sort_order')
    
    for line in top_lines:
        result.extend(_explode_line(line, qty_multiplier, depth, parent_path))
    
    return result


def _explode_line(line, qty_multiplier, depth, parent_path):
    """Recursively explode a single BOM line"""
    from .models import BOMLine
    
    rolled_qty = line.quantity * qty_multiplier
    current_path = f"{parent_path} > {line.item.item_code}" if parent_path else line.item.item_code
    
    children = line.children.all().order_by('sort_order')
    
    if children.exists():
        result = []
        if not line.is_phantom:
            result.append({
                'item': line.item,
                'item_id': str(line.item.id),
                'item_code': line.item.item_code,
                'item_name': line.item.name,
                'item_class': line.item_class,
                'quantity': rolled_qty,
                'uom': line.uom,
                'depth': depth,
                'is_assembly': True,
                'path': current_path,
            })
        for child in children:
            result.extend(_explode_line(child, rolled_qty, depth + 1, current_path))
        return result
    else:
        return [{
            'item': line.item,
            'item_id': str(line.item.id),
            'item_code': line.item.item_code,
            'item_name': line.item.name,
            'item_class': line.item_class,
            'quantity': rolled_qty,
            'uom': line.uom,
            'depth': depth,
            'is_assembly': False,
            'path': current_path,
        }]


def clone_bom_revision(existing_bom, new_version, effective_date, description='', created_by=None):
    """Clone an existing BOM into a new revision."""
    from .models import EngineeringBOM, BOMLine
    
    with transaction.atomic():
        new_bom = EngineeringBOM.objects.create(
            name=existing_bom.name,
            parent_item=existing_bom.parent_item,
            version=new_version,
            description=description or f"Cloned from {existing_bom.bom_number}",
            effective_date=effective_date,
            status='DRAFT',
            is_active=False,
            created_by=created_by,
            tenant=existing_bom.tenant,
        )
        
        for line in existing_bom.lines.filter(parent_line__isnull=True):
            _clone_line(line, new_bom, parent=None)
        
        existing_bom.is_active = False
        existing_bom.obsolete_date = timezone.now().date()
        existing_bom.status = 'OBSOLETE'
        existing_bom.save(update_fields=['is_active', 'obsolete_date', 'status'])
        
        return new_bom


def _clone_line(line, new_bom, parent=None):
    """Recursively clone a BOM line and its children."""
    from .models import BOMLine
    
    new_line = BOMLine.objects.create(
        bom=new_bom,
        parent_line=parent,
        item=line.item,
        quantity=line.quantity,
        uom=line.uom,
        item_class=line.item_class,
        reference_designator=line.reference_designator,
        note=line.note,
        is_phantom=line.is_phantom,
        effective_date=line.effective_date,
        obsolete_date=line.obsolete_date,
        sort_order=line.sort_order,
    )
    
    for child in line.children.all():
        _clone_line(child, new_bom, parent=new_line)
    
    return new_line


def get_bom_line_count(bom):
    """Get total number of lines (including nested) in a BOM"""
    def count_lines(line):
        count = 1
        for child in line.children.all():
            count += count_lines(child)
        return count
    
    total = 0
    for line in bom.lines.filter(parent_line__isnull=True):
        total += count_lines(line)
    return total


def get_bom_depth(bom):
    """Get the maximum depth of a BOM tree"""
    def get_depth(line, current_depth=0):
        max_depth = current_depth
        for child in line.children.all():
            child_depth = get_depth(child, current_depth + 1)
            max_depth = max(max_depth, child_depth)
        return max_depth
    
    max_depth = 0
    for line in bom.lines.filter(parent_line__isnull=True):
        depth = get_depth(line, 0)
        max_depth = max(max_depth, depth)
    return max_depth


# ─── BOM Snapshot Functions ──────────────────────────────────────────────────

def create_bom_snapshot(bom):
    """
    Create a frozen snapshot of a BOM with all its lines.
    Used when releasing a package.
    """
    from .models import BOMLine
    
    def collect_lines(line):
        return {
            'id': str(line.id),
            'item_id': str(line.item.id),
            'item_code': line.item.item_code,
            'item_name': line.item.name,
            'item_class': line.item_class,
            'quantity': float(line.quantity),
            'uom': line.uom,
            'reference_designator': line.reference_designator,
            'note': line.note,
            'is_phantom': line.is_phantom,
            'children': [collect_lines(child) for child in line.children.all().order_by('sort_order')]
        }
    
    top_lines = bom.lines.filter(parent_line__isnull=True).order_by('sort_order')
    
    snapshot = {
        'bom_id': str(bom.id),
        'bom_number': bom.bom_number,
        'name': bom.name,
        'version': bom.version,
        'effective_date': bom.effective_date.isoformat() if bom.effective_date else None,
        'parent_item': {
            'id': str(bom.parent_item.id),
            'item_code': bom.parent_item.item_code,
            'name': bom.parent_item.name,
        },
        'lines': [collect_lines(line) for line in top_lines],
        'created_at': timezone.now().isoformat(),
    }
    
    return snapshot


def explode_bom_from_snapshot(snapshot):
    """
    Explode a BOM snapshot (not from live database).
    Returns flat list of all components.
    """
    result = []
    
    def process_lines(lines, qty_multiplier=1, path=''):
        for line in lines:
            current_qty = line['quantity'] * qty_multiplier
            current_path = f"{path} > {line['item_code']}" if path else line['item_code']
            
            if line.get('children'):
                if line.get('is_phantom'):
                    process_lines(line['children'], current_qty, current_path)
                else:
                    result.append({
                        'item_id': line['item_id'],
                        'item_code': line['item_code'],
                        'item_name': line['item_name'],
                        'item_class': line['item_class'],
                        'quantity': current_qty,
                        'uom': line['uom'],
                        'is_assembly': True,
                        'path': current_path,
                    })
                    process_lines(line['children'], current_qty, current_path)
            else:
                result.append({
                    'item_id': line['item_id'],
                    'item_code': line['item_code'],
                    'item_name': line['item_name'],
                    'item_class': line['item_class'],
                    'quantity': current_qty,
                    'uom': line['uom'],
                    'is_assembly': False,
                    'path': current_path,
                })
    
    process_lines(snapshot.get('lines', []))
    return result


# ─── Package Management Functions ────────────────────────────────────────────

def create_package_from_bom(project, bom, version, documents=None, created_by=None,
                             change_description=''):
    """Create a new package from a BOM. Package starts as DRAFT."""
    from .models import EngineeringPackage, EngineeringPackageDocument

    if EngineeringPackage.objects.filter(project=project, version=version).exists():
        raise ValueError(f"Package version {version} already exists for this project")

    package = EngineeringPackage.objects.create(
        project=project,
        source_bom=bom,
        name=f"Engineering Package for {project.name}",
        version=version,
        status='DRAFT',
        change_description=change_description,
        created_by=created_by,
        tenant=project.tenant,
    )

    if documents:
        for doc in documents:
            EngineeringPackageDocument.objects.create(package=package, document=doc)

    return package


def create_and_release_package(project, bom, revision, documents=None, created_by=None,
                                change_description=''):
    """
    Convenience wrapper used by BOMViewSet.create_package(): creates a DRAFT
    package and immediately releases it (freezes the snapshot).
    """
    package = create_package_from_bom(
        project=project,
        bom=bom,
        version=revision,
        documents=documents,
        created_by=created_by,
        change_description=change_description,
    )
    return release_package(package, created_by)


def release_package(package, released_by):
    """Release a package - creates frozen snapshot and notifies PM. ONLY through this service."""
    from .models import PackageHistoryEntry
    from apps.notifications.services import create_notification

    if package.status != 'DRAFT':
        raise ValueError(f"Only DRAFT packages can be released (current: {package.status})")
    if not package.source_bom:
        raise ValueError("Package has no source BOM to create snapshot from")

    package.bom_snapshot = create_bom_snapshot(package.source_bom)
    package.document_snapshots = package._get_document_snapshots()
    package.status = 'RELEASED'
    package.released_by = released_by
    package.released_at = timezone.now()
    package.save()

    PackageHistoryEntry.objects.create(
        package=package,
        from_status='DRAFT',
        to_status='RELEASED',
        changed_by=released_by,
        comment=f"Package released by {released_by.get_full_name() or released_by.username}"
    )

    if package.project.project_manager:
        create_notification(
            user=package.project.project_manager,
            title=f"📦 Package Released: {package.package_number}",
            message=f"{package.project.name} has a new package ready for review: {package.version}",
            link=f"/engineering/packages/{package.id}",
            notification_type='INFO',
            created_by=released_by,
        )

    return package


def accept_package(package, accepted_by, notes=''):
    """Accept a package - creates material requirements. ONLY through this service."""
    from .models import PackageHistoryEntry
    from apps.notifications.services import create_notification

    if package.status != 'RELEASED':
        raise ValueError(f"Only RELEASED packages can be accepted (current: {package.status})")

    project = package.project
    if project.active_package and project.active_package.id != package.id:
        old_package = project.active_package
        old_status = old_package.status
        old_package.status = 'OBSOLETE'
        old_package.save(update_fields=['status'])
        PackageHistoryEntry.objects.create(
            package=old_package,
            from_status=old_status,
            to_status='OBSOLETE',
            changed_by=accepted_by,
            comment=f"Superseded by {package.package_number}"
        )

    package.status = 'ACCEPTED'
    package.accepted_by = accepted_by
    package.accepted_at = timezone.now()
    package.acceptance_notes = notes
    package.save()

    PackageHistoryEntry.objects.create(
        package=package,
        from_status='RELEASED',
        to_status='ACCEPTED',
        changed_by=accepted_by,
        comment=notes or "Package accepted"
    )

    project.active_package = package
    project.save(update_fields=['active_package'])

    create_material_requirements_from_package(package)

    from apps.accounts.models import TenantUser
    engineering_users = TenantUser.objects.filter(
        tenant=project.tenant,
        role__in=['engineering', 'manager'],
        is_active=True
    ).values_list('user_id', flat=True)

    if engineering_users:
        create_notification(
            user=None,
            title=f"✅ Package Accepted: {package.package_number}",
            message=f"{project.name} has accepted {package.version}",
            link=f"/engineering/packages/{package.id}",
            notification_type='SUCCESS',
            created_by=accepted_by,
            recipient_ids=list(engineering_users),
        )

    return package


def reject_package(package, rejected_by, reason=''):
    """Reject a RELEASED package. ONLY through this service."""
    from .models import PackageHistoryEntry
    from apps.notifications.services import create_notification

    if package.status != 'RELEASED':
        raise ValueError(f"Only RELEASED packages can be rejected (current: {package.status})")

    package.status = 'REJECTED'
    package.rejected_by = rejected_by
    package.rejected_at = timezone.now()
    package.rejection_reason = reason
    package.save()

    PackageHistoryEntry.objects.create(
        package=package,
        from_status='RELEASED',
        to_status='REJECTED',
        changed_by=rejected_by,
        comment=reason or "Package rejected"
    )

    from apps.accounts.models import TenantUser
    engineering_users = TenantUser.objects.filter(
        tenant=package.project.tenant,
        role__in=['engineering', 'manager'],
        is_active=True
    ).values_list('user_id', flat=True)

    if engineering_users:
        create_notification(
            user=None,
            title=f"❌ Package Rejected: {package.package_number}",
            message=f"{package.project.name} has rejected {package.version}. Reason: {reason}",
            link=f"/engineering/packages/{package.id}",
            notification_type='WARNING',
            created_by=rejected_by,
            recipient_ids=list(engineering_users),
        )

    return package


def obsolete_package(package, obsoleted_by, reason=''):
    """Mark a package as OBSOLETE. ONLY through this service."""
    from .models import PackageHistoryEntry

    if package.status == 'OBSOLETE':
        return package

    old_status = package.status  # capture BEFORE overwriting
    package.status = 'OBSOLETE'
    package.save(update_fields=['status'])

    PackageHistoryEntry.objects.create(
        package=package,
        from_status=old_status,
        to_status='OBSOLETE',
        changed_by=obsoleted_by,
        comment=reason or "Package obsoleted"
    )

    return package


# ─── Change Notice Functions ─────────────────────────────────────────────────

def create_change_notice(old_package, new_bom, new_revision, change_reason, created_by):
    """
    Engineering creates a change notice when they revise the BOM.
    """
    from .models import EngineeringPackage, EngineeringPackageChangeNotice
    from apps.notifications.services import create_notification
    
    if old_package.status != 'ACCEPTED':
        raise ValueError(f"Only ACCEPTED packages can be changed (status: {old_package.status})")
    
    # Create new package
    new_package = EngineeringPackage.objects.create(
        project=old_package.project,
        source_bom=new_bom,
        name=f"Engineering Package for {old_package.project.name}",
        version=new_revision,
        status='DRAFT',
        change_description=change_reason,
        previous_package=old_package,
        created_by=created_by,
        tenant=old_package.project.tenant,
    )
    
    # Create change notice
    ecn = EngineeringPackageChangeNotice.objects.create(
        package=old_package,
        new_package=new_package,
        title=f"BOM Revision Update: {old_package.source_bom.bom_number} → {new_bom.bom_number}",
        description=change_reason,
        reason=change_reason,
        requested_by=created_by,
        priority='MEDIUM',
        status='PENDING',
    )
    
    # Notify PM that change is pending
    if old_package.project.project_manager:
        create_notification(
            user=old_package.project.project_manager,
            title=f"🔄 Engineering Change Notice: {ecn.ecn_number}",
            message=f"Engineering proposes revision {new_revision} for {old_package.name}",
            link=f"/projects/{old_package.project.id}/ecn/{ecn.id}",
            notification_type='WARNING',
            created_by=created_by,
        )
    
    return ecn


def review_change_notice(ecn, decision, reviewed_by, notes=''):
    """
    PM reviews and decides on the change.

    ecn.new_package is always DRAFT at this point (create_change_notice never
    releases it) — so any status change on it MUST go through the
    transition-aware service functions below, never a direct
    `new_package.status = 'X'; new_package.save()` assignment. That direct
    assignment (old REJECTED branch) skipped ALLOWED_TRANSITIONS validation
    entirely and never wrote a PackageHistoryEntry, silently breaking the audit
    trail. Fixed by routing through obsolete_package()/release_package().
    """
    from apps.notifications.services import create_notification
    
    valid_decisions = ['APPROVED', 'REJECTED', 'DEFERRED']
    if decision not in valid_decisions:
        raise ValueError(f"Decision must be one of: {', '.join(valid_decisions)}")
    
    ecn.status = decision
    ecn.reviewed_by = reviewed_by
    ecn.reviewed_at = timezone.now()
    
    if decision == 'APPROVED':
        # Release the new package
        new_package = ecn.new_package
        if not new_package:
            raise ValueError("No new package linked to this change notice")
        
        # Release the new package (this creates the snapshot, validates
        # DRAFT -> RELEASED via ALLOWED_TRANSITIONS, and writes history)
        release_package(new_package, reviewed_by)
        
        # Notify engineering of approval
        if ecn.requested_by:
            create_notification(
                user=ecn.requested_by,
                title=f"✅ ECN {ecn.ecn_number} Approved",
                message=f"Project {ecn.package.project.name} has approved the change.",
                notification_type='SUCCESS',
                created_by=reviewed_by,
            )
    
    elif decision == 'REJECTED':
        # FIXED: new_package is still DRAFT here — DRAFT -> REJECTED isn't a
        # legal transition (see ALLOWED_TRANSITIONS on EngineeringPackage).
        # A proposal that never made it to RELEASED and got turned down is
        # correctly modeled as OBSOLETE, not REJECTED (REJECTED is reserved
        # for packages that were actually released and then turned down via
        # reject_package()). Routing through obsolete_package() also restores
        # the PackageHistoryEntry audit record the old code silently skipped.
        if ecn.new_package:
            obsolete_package(
                ecn.new_package,
                reviewed_by,
                reason=notes or f"ECN {ecn.ecn_number} rejected before release"
            )
        
        # Notify engineering of rejection
        if ecn.requested_by:
            create_notification(
                user=ecn.requested_by,
                title=f"❌ ECN {ecn.ecn_number} Rejected",
                message=f"Project {ecn.package.project.name} rejected the change. Reason: {notes}",
                notification_type='WARNING',
                created_by=reviewed_by,
            )
    
    elif decision == 'DEFERRED':
        # No package state change — just parked for later review.
        if ecn.requested_by:
            create_notification(
                user=ecn.requested_by,
                title=f"⏳ ECN {ecn.ecn_number} Deferred",
                message=f"Project {ecn.package.project.name} has deferred the change decision.",
                notification_type='INFO',
                created_by=reviewed_by,
            )
    
    ecn.save()
    return ecn


def create_material_requirements_from_package(package):
    """
    Create material requirements for a project from an accepted engineering package.
    Uses the frozen snapshot, not the live BOM.
    """
    from apps.projects.models import ProjectMaterialRequirement
    from apps.engineering.models import EngineeringItemMaster
    
    if package.status != 'ACCEPTED':
        raise ValueError(f"Package must be ACCEPTED to create requirements (status: {package.status})")
    
    if not package.bom_snapshot:
        raise ValueError("Package has no BOM snapshot")
    
    # Explode from snapshot (not from live BOM)
    exploded_items = explode_bom_from_snapshot(package.bom_snapshot)
    
    if not exploded_items:
        return {'created': 0, 'warning': 'BOM has no components'}
    
    created_count = 0
    skipped_count = 0
    
    for item_data in exploded_items:
        item_id = item_data.get('item_id')
        qty = item_data.get('quantity')
        
        if not item_id:
            skipped_count += 1
            continue
        
        try:
            item = EngineeringItemMaster.objects.get(id=item_id)
        except EngineeringItemMaster.DoesNotExist:
            skipped_count += 1
            continue
        
        # Check if this material requirement already exists
        existing = ProjectMaterialRequirement.objects.filter(
            project=package.project,
            item=item,
            status__in=['REQUIRED', 'RESERVED', 'PURCHASED']
        ).first()
        
        if existing:
            existing.required_qty += qty
            existing.save(update_fields=['required_qty'])
            created_count += 1
        else:
            ProjectMaterialRequirement.objects.create(
                project=package.project,
                item=item,
                bom_requirement=package,
                required_qty=qty,
                status='REQUIRED',
                created_by=package.created_by,
            )
            created_count += 1
    
    return {
        'created': created_count,
        'skipped': skipped_count,
        'total_components': len(exploded_items),
        'package_id': str(package.id),
        'project_id': str(package.project.id),
    }