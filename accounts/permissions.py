"""
Central permission and tenant-isolation helpers for EduTrack.

All role, membership and institution checks used by views live here so that
authorisation rules are applied consistently instead of being re-implemented
(and accidentally weakened) in each view.

Security principles:
- A user only acts inside an *approved* membership.
- Platform administrators are the only users allowed to perform platform-wide
  operations; they do not gain access to an institution's private academic data.
- Query sets are always narrowed to the active institution and, where relevant,
  to the courses/students the user is explicitly assigned to.
"""

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .models import MembershipStatus, Role


def user_is_platform_admin(user):
    """Return True only for superusers or approved platform administrators."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    # Cached on the user instance; Django's auth middleware builds a fresh
    # user object per request, so the cache never outlives one request.
    cached = getattr(user, '_platform_admin_cache', None)
    if cached is not None:
        return cached
    result = user.memberships.filter(
        role=Role.PLATFORM_ADMIN,
        status=MembershipStatus.APPROVED,
    ).exists()
    user._platform_admin_cache = result
    return result


def approved_institution_memberships(user):
    """
    Approved memberships at active institutions, in a reusable query.

    A deactivated institution (``is_active=False``) must not grant access to
    its academic records, so it is excluded from every membership lookup.
    """
    return user.memberships.select_related(
        'institution', 'department', 'programme'
    ).filter(
        status=MembershipStatus.APPROVED,
        institution__is_active=True,
    )


def get_active_membership(request):
    """Return the user's approved membership for the active institution."""
    if not request.user.is_authenticated:
        return None

    # Resolved once per request; several views and the template context
    # processor ask for the active membership during a single request.
    cached = getattr(request, '_active_membership_cache', _MEMBERSHIP_UNSET)
    if cached is not _MEMBERSHIP_UNSET:
        return cached

    membership_id = request.session.get('active_membership_id')
    memberships = approved_institution_memberships(request.user)

    if membership_id:
        active = memberships.filter(id=membership_id).first()
        if active:
            request._active_membership_cache = active
            return active

    active = memberships.first()
    if active:
        request.session['active_membership_id'] = active.id
    request._active_membership_cache = active
    return active


class _MembershipUnset:
    """Sentinel distinguishing 'not cached' from a cached ``None``."""


_MEMBERSHIP_UNSET = _MembershipUnset()


def get_active_institution(request):
    """Return the institution of the active approved membership, or None."""
    membership = get_active_membership(request)
    return membership.institution if membership else None


def require_membership(request):
    """Return the active membership or raise PermissionDenied."""
    membership = get_active_membership(request)
    if not membership:
        raise PermissionDenied('An approved institutional membership is required.')
    return membership


def get_request_role(request):
    """Return the effective role for the current request."""
    membership = get_active_membership(request)
    if membership:
        return membership.role
    if user_is_platform_admin(request.user):
        return Role.PLATFORM_ADMIN
    return None


def can_access_institution(user, institution):
    """Check that a user has an approved membership in an active institution."""
    if not user or not user.is_authenticated or institution is None:
        return False
    if not institution.is_active:
        # A deactivated institution must not serve any of its records.
        return False
    if user.is_superuser:
        # Superusers may operate the platform, but institution-scoped views
        # still require an explicit membership for private academic records.
        return user.memberships.filter(
            institution=institution,
            status=MembershipStatus.APPROVED,
        ).exists()
    return user.memberships.filter(
        institution=institution,
        status=MembershipStatus.APPROVED,
    ).exists()


def scope_to_institution(queryset, institution):
    """Restrict an institution-owned query set to a single institution."""
    if institution is None:
        return queryset.none()
    return queryset.filter(institution=institution)


def get_institution_object_or_404(model, institution, **lookup):
    """Fetch an object only when it belongs to the given institution."""
    if institution is None:
        raise PermissionDenied('No active institution selected.')
    return get_object_or_404(model, institution=institution, **lookup)


def instructor_offerings(user, institution, active_only=False):
    """Course offerings explicitly assigned to an instructor."""
    from academics.models import CourseOffering

    queryset = CourseOffering.objects.filter(
        institution=institution,
        instructor=user,
    )
    if active_only:
        queryset = queryset.filter(is_active=True)
    return queryset


def instructor_student_ids(user, institution):
    """IDs of students enrolled in any course taught by the instructor."""
    from academics.models import Enrollment

    return list(
        Enrollment.objects.filter(
            institution=institution,
            course_offering__instructor=user,
            status=Enrollment.EnrollmentStatus.ACTIVE,
        ).values_list('student_id', flat=True).distinct()
    )


def adviser_assignments(user, institution, active_only=True):
    """Active advisee assignments belonging to an adviser."""
    from advising.models import AdviserAssignment

    queryset = AdviserAssignment.objects.filter(
        institution=institution,
        adviser=user,
    )
    if active_only:
        queryset = queryset.filter(is_active=True)
    return queryset


def adviser_student_ids(user, institution):
    """IDs of students formally assigned to an adviser."""
    return list(
        adviser_assignments(user, institution)
        .values_list('student_id', flat=True)
    )


def adviser_students(user, institution):
    """User queryset of students formally assigned to an adviser."""
    from .models import User

    return User.objects.filter(
        adviser_assignments__adviser=user,
        adviser_assignments__institution=institution,
        adviser_assignments__is_active=True,
    ).distinct()


def ensure_offering_access(user, offering, institution):
    """
    Verify that an instructor teaches the offering, or that an institution
    administrator manages its institution. Raises PermissionDenied otherwise.
    """
    if offering.institution_id != getattr(institution, 'id', None):
        raise PermissionDenied('This course belongs to another institution.')

    role = user.get_role_in(institution)
    if role == Role.INSTITUTION_ADMIN:
        return
    if role == Role.INSTRUCTOR and offering.instructor_id == user.id:
        return
    raise PermissionDenied('You are not assigned to this course.')


def ensure_student_assigned_to_adviser(user, student, institution):
    """Verify that a student is actively assigned to the adviser."""
    from advising.models import AdviserAssignment

    if not AdviserAssignment.objects.filter(
        institution=institution,
        adviser=user,
        student=student,
        is_active=True,
    ).exists():
        raise PermissionDenied('This student is not assigned to you.')
