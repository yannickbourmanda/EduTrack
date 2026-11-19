"""
Role-based access-control decorators for EduTrack.

Views are protected with explicit role lists. Permission checks happen on the
server; navigation visibility is only a usability aid and is never relied on
for security.
"""

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from dashboard.models import AuditLog, record_audit

from .models import Role
from .permissions import (
    get_active_institution,
    get_active_membership,
    user_is_platform_admin,
)


def _log_access_denied(request, reason):
    """Record an ACCESS_DENIED audit entry for an authenticated user."""
    user = request.user
    if not user or not user.is_authenticated:
        return
    try:
        membership = get_active_membership(request)
        institution = membership.institution if membership else None
        record_audit(
            request=request,
            institution=institution,
            user=user,
            action=AuditLog.ActionChoices.ACCESS_DENIED,
            description=f'{request.path}: {reason}',
        )
    except Exception:  # pragma: no cover - audit must never block the 403
        pass


def role_required(allowed_roles):
    """
    Require one of ``allowed_roles`` under the user's active membership.

    Platform administrators are accepted only when ``PLATFORM_ADMIN`` is in the
    allowed list. A superuser no longer silently bypasses institution checks.
    """
    allowed_roles = set(allowed_roles)

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                messages.warning(request, 'Please sign in to continue.')
                return redirect('accounts:login')

            if user_is_platform_admin(request.user) and Role.PLATFORM_ADMIN in allowed_roles:
                return view_func(request, *args, **kwargs)

            if user_is_platform_admin(request.user):
                # Platform administrators do not hold institution-scoped
                # privileges and must not read private academic records.
                _log_access_denied(
                    request,
                    'platform administrators cannot access institutional '
                    'academic records.',
                )
                raise PermissionDenied(
                    'Platform administrators cannot access institutional '
                    'academic records.'
                )

            membership = get_active_membership(request)
            if not membership:
                return redirect('accounts:pending_approval')

            if membership.role not in allowed_roles:
                _log_access_denied(
                    request,
                    f'role {membership.role} does not grant access.',
                )
                raise PermissionDenied(
                    'Your role does not grant access to this page.'
                )

            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator


def platform_admin_required(view_func):
    """Restrict a view to authorised platform administrators."""

    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.warning(request, 'Please sign in to continue.')
            return redirect('accounts:login')
        if not user_is_platform_admin(request.user):
            _log_access_denied(
                request, 'platform administrator privileges are required.'
            )
            raise PermissionDenied(
                'Platform administrator privileges are required.'
            )
        return view_func(request, *args, **kwargs)

    return _wrapped_view


# Re-exported for backwards compatibility with existing imports.
__all__ = [
    'role_required',
    'platform_admin_required',
    'get_active_membership',
    'get_active_institution',
]
