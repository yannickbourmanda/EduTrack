"""
Accounts views - EduTrack
Authentication, registration, institutional membership review and profile
management.

Security notes:
- Membership decisions are POST-only, CSRF-protected and scoped to the
  administrator's own institution.
- No unauthenticated shortcut login exists.
"""

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from dashboard.models import AuditLog, record_audit
from institutions.models import Institution

from .decorators import get_active_membership, role_required
from .forms import LoginForm, UserRegistrationForm
from .models import Membership, MembershipStatus, Role, User
from .permissions import get_active_institution, user_is_platform_admin

# Brute-force guard: after this many failed attempts for one email address
# (or from one client address) further sign-ins are refused for a window.
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_IP_LIMIT = 20
LOGIN_LOCKOUT_SECONDS = 15 * 60


def _login_is_locked(request, email):
    """Return True when sign-in is temporarily refused for this email/IP."""
    ip = request.META.get('REMOTE_ADDR') or 'unknown'
    email_fails = cache.get(f'edutrack:login-fail:{email}', 0)
    ip_fails = cache.get(f'edutrack:login-fail-ip:{ip}', 0)
    if email_fails >= LOGIN_ATTEMPT_LIMIT or ip_fails >= LOGIN_IP_LIMIT:
        record_audit(
            request=request,
            institution=None,
            user=None,
            action=AuditLog.ActionChoices.LOGIN_FAILED,
            description=(
                f'Sign-in refused for {email}: temporary lockout active.'
            ),
        )
        messages.error(
            request,
            'Too many failed sign-in attempts. Please try again in '
            f'{LOGIN_LOCKOUT_SECONDS // 60} minutes.',
        )
        return True
    return False


def _register_login_failure(request, email):
    """Count one failed attempt against both the email and the client IP."""
    ip = request.META.get('REMOTE_ADDR') or 'unknown'
    email_key = f'edutrack:login-fail:{email}'
    ip_key = f'edutrack:login-fail-ip:{ip}'
    cache.set(
        email_key,
        cache.get(email_key, 0) + 1,
        LOGIN_LOCKOUT_SECONDS,
    )
    cache.set(
        ip_key,
        cache.get(ip_key, 0) + 1,
        LOGIN_LOCKOUT_SECONDS,
    )


def _clear_login_failures(request, email):
    """Reset the counters after a successful authentication."""
    ip = request.META.get('REMOTE_ADDR') or 'unknown'
    cache.delete(f'edutrack:login-fail:{email}')
    cache.delete(f'edutrack:login-fail-ip:{ip}')


# ---------------------------------------------------------------------------
# Password management (built-in Django views, with audit logging)
# ---------------------------------------------------------------------------

class AuditedPasswordChangeView(auth_views.PasswordChangeView):
    """Record a PASSWORD_CHANGED entry when the password actually changes."""

    def form_valid(self, form):
        response = super().form_valid(form)
        membership = get_active_membership(self.request)
        record_audit(
            request=self.request,
            institution=membership.institution if membership else None,
            action=AuditLog.ActionChoices.PASSWORD_CHANGED,
            description=(
                f'User {self.request.user.email} changed their password.'
            ),
        )
        return response


class AuditedPasswordResetView(auth_views.PasswordResetView):
    """Record a PASSWORD_RESET_REQUESTED entry for a known account."""

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(
            request=self.request,
            institution=None,
            user=None,
            action=AuditLog.ActionChoices.PASSWORD_RESET_REQUESTED,
            description=(
                'Password reset requested for '
                f'{form.cleaned_data["email"].lower()}.'
            ),
        )
        return response


def register_view(request):
    """Public registration with a pending institutional membership request."""
    if request.user.is_authenticated:
        return redirect('dashboard:home')

    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                institution = form.cleaned_data['institution']
                requested_role = form.cleaned_data['requested_role']

                membership = Membership.objects.create(
                    user=user,
                    institution=institution,
                    role=requested_role,
                    status=MembershipStatus.PENDING,
                )

                record_audit(
                    request=request,
                    institution=institution,
                    user=user,
                    action=AuditLog.ActionChoices.REGISTRATION_REQUESTED,
                    description=(
                        f'New user registered: {user.email} requesting '
                        f'{requested_role} role.'
                    ),
                )

            login(request, user)
            request.session['active_membership_id'] = membership.id
            messages.success(
                request,
                f'Your request to join {institution.name} as a '
                f'{membership.get_role_display()} has been submitted for review.'
            )
            return redirect('accounts:pending_approval')
    else:
        form = UserRegistrationForm()

    return render(request, 'accounts/register.html', {'form': form})


def login_view(request):
    """Email/password authentication with a small brute-force guard."""
    if request.user.is_authenticated:
        return redirect('dashboard:home')

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email'].lower()
            password = form.cleaned_data['password']

            if _login_is_locked(request, email):
                return render(request, 'accounts/login.html', {'form': form})

            user = authenticate(request, username=email, password=password)

            if user is not None:
                login(request, user)
                _clear_login_failures(request, email)
                # Only memberships at active institutions may become the
                # active tenant; a deactivated institution cannot serve data.
                approved_membership = user.memberships.filter(
                    status=MembershipStatus.APPROVED,
                    institution__is_active=True,
                ).select_related('institution').first()

                if approved_membership:
                    request.session['active_membership_id'] = approved_membership.id
                    record_audit(
                        request=request,
                        institution=approved_membership.institution,
                        user=user,
                        action=AuditLog.ActionChoices.LOGIN,
                        description=f'User {user.email} signed in.',
                    )
                    messages.success(
                        request,
                        f'Welcome back, {user.first_name or user.email}.'
                    )
                    return redirect('dashboard:home')

                if user_is_platform_admin(user):
                    record_audit(
                        request=request,
                        institution=None,
                        user=user,
                        action=AuditLog.ActionChoices.LOGIN,
                        description=(
                            f'Platform administrator {user.email} signed in.'
                        ),
                    )
                    messages.success(request, 'Welcome back.')
                    return redirect('dashboard:home')

                pending = user.memberships.first()
                if pending:
                    request.session['active_membership_id'] = pending.id
                return redirect('accounts:pending_approval')

            _register_login_failure(request, email)
            record_audit(
                request=request,
                institution=None,
                user=None,
                action=AuditLog.ActionChoices.LOGIN_FAILED,
                description=(
                    f'Failed sign-in attempt for {email} '
                    f'({request.META.get("REMOTE_ADDR") or "unknown origin"}).'
                ),
            )
            messages.error(request, 'Invalid email address or password.')
    else:
        form = LoginForm()

    return render(request, 'accounts/login.html', {'form': form})


@login_required
@require_POST
def logout_view(request):
    """Sign out and clear session state."""
    membership = get_active_membership(request)
    record_audit(
        request=request,
        institution=membership.institution if membership else None,
        action=AuditLog.ActionChoices.LOGOUT,
        description=f'User {request.user.email} signed out.',
    )
    logout(request)
    messages.info(request, 'You have been signed out.')
    return redirect('accounts:login')


@login_required
def pending_approval_view(request):
    """Explain the status of a pending, rejected or suspended membership."""
    if user_is_platform_admin(request.user):
        return redirect('dashboard:home')

    membership = get_active_membership(request)
    if not membership:
        membership = request.user.memberships.select_related('institution').first()

    # An approved membership at a *deactivated* institution must not redirect
    # back to the dashboard, which would send the user here again (loop).
    institution_unavailable = bool(
        membership and not membership.institution.is_active
    )
    if (
        membership
        and membership.status == MembershipStatus.APPROVED
        and not institution_unavailable
    ):
        return redirect('dashboard:home')

    return render(
        request,
        'accounts/pending_approval.html',
        {
            'membership': membership,
            'institution_unavailable': institution_unavailable,
        },
    )


@login_required
@require_POST
def switch_institution_view(request, membership_id):
    """Switch the active tenant; only the user's own memberships are valid."""
    membership = get_object_or_404(
        request.user.memberships.select_related('institution'),
        id=membership_id,
        status=MembershipStatus.APPROVED,
        institution__is_active=True,
    )
    request.session['active_membership_id'] = membership.id
    messages.success(
        request,
        f'Switched to {membership.institution.name} '
        f'({membership.get_role_display()}).'
    )
    return redirect('dashboard:home')


@login_required
@role_required([Role.INSTITUTION_ADMIN])
def membership_management_view(request):
    """Review membership requests for the administrator's own institution."""
    institution = get_active_institution(request)
    requests_qs = Membership.objects.filter(
        institution=institution
    ).select_related(
        'user', 'institution', 'department', 'programme', 'reviewed_by'
    )

    status_filter = request.GET.get('status', MembershipStatus.PENDING)
    if status_filter in dict(MembershipStatus.choices):
        requests_qs = requests_qs.filter(status=status_filter)
    else:
        status_filter = ''

    search = request.GET.get('q', '').strip()
    if search:
        requests_qs = requests_qs.filter(
            Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(user__email__icontains=search)
            | Q(user__matriculation_number__icontains=search)
            | Q(user__staff_id__icontains=search)
        )

    paginator = Paginator(requests_qs.order_by('-requested_at'), 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'accounts/membership_list.html', {
        'memberships': page_obj,
        'page_obj': page_obj,
        'status_filter': status_filter,
        'search': search,
        'institution': institution,
        'status_choices': MembershipStatus.choices,
        'role_choices': Role.choices,
    })


@login_required
@role_required([Role.INSTITUTION_ADMIN])
@require_POST
def approve_membership_view(request, membership_id):
    """Approve a membership request inside the administrator's institution."""
    institution = get_active_institution(request)
    membership = get_object_or_404(
        Membership,
        id=membership_id,
        institution=institution,
    )

    with transaction.atomic():
        membership.approve(request.user)
        record_audit(
            request=request,
            institution=institution,
            action=AuditLog.ActionChoices.ROLE_APPROVAL,
            description=(
                f'Approved {membership.user.email} as '
                f'{membership.get_role_display()}.'
            ),
        )

    messages.success(
        request,
        f'Approved {membership.user.get_full_name() or membership.user.email} '
        f'as {membership.get_role_display()}.'
    )
    return redirect('accounts:membership_management')


@login_required
@role_required([Role.INSTITUTION_ADMIN])
@require_POST
def reject_membership_view(request, membership_id):
    """Reject a membership request inside the administrator's institution."""
    institution = get_active_institution(request)
    membership = get_object_or_404(
        Membership,
        id=membership_id,
        institution=institution,
    )

    reason = request.POST.get(
        'rejection_reason',
        'Did not meet institutional verification criteria.',
    ).strip() or 'Did not meet institutional verification criteria.'

    with transaction.atomic():
        membership.reject(request.user, reason=reason)
        record_audit(
            request=request,
            institution=institution,
            action=AuditLog.ActionChoices.ROLE_REJECTION,
            description=(
                f'Rejected {membership.user.email} membership request: {reason}'
            ),
        )

    messages.warning(
        request,
        f'Rejected the membership request for '
        f'{membership.user.get_full_name() or membership.user.email}.'
    )
    return redirect('accounts:membership_management')


@login_required
def profile_view(request):
    """View and update the signed-in user's basic profile."""
    user = request.user

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        user.first_name = first_name[:150]
        user.last_name = last_name[:150]
        user.phone_number = request.POST.get('phone_number', '').strip()[:20]
        user.profile_bio = request.POST.get('profile_bio', '').strip()[:2000]
        user.save(update_fields=[
            'first_name', 'last_name', 'phone_number', 'profile_bio', 'updated_at'
        ])
        messages.success(request, 'Profile updated.')
        return redirect('accounts:profile')

    return render(request, 'accounts/profile.html', {
        'profile_user': user,
        'active_membership': get_active_membership(request),
    })


# ---------------------------------------------------------------------------
# Membership lifecycle management (suspend / reactivate / change role)
# ---------------------------------------------------------------------------

def _last_active_admin_membership(institution, exclude_membership_id=None):
    """
    Return True when ``membership`` is the only approved institution
    administrator of its institution. Offboarding or demoting that member
    would leave the tenant with no way to administer users.
    """
    if exclude_membership_id is None:
        return not Membership.objects.filter(
            institution=institution,
            role=Role.INSTITUTION_ADMIN,
            status=MembershipStatus.APPROVED,
        ).exists()
    return not Membership.objects.filter(
        institution=institution,
        role=Role.INSTITUTION_ADMIN,
        status=MembershipStatus.APPROVED,
    ).exclude(id=exclude_membership_id).exists()


def _membership_admin_context(request, membership_id):
    """Resolve the target membership inside the administrator's institution."""
    institution = get_active_institution(request)
    membership = get_object_or_404(
        Membership.objects.select_related('user', 'institution'),
        id=membership_id,
        institution=institution,
    )
    return institution, membership


@login_required
@role_required([Role.INSTITUTION_ADMIN])
@require_POST
def suspend_membership_view(request, membership_id):
    """Suspend an approved member of the administrator's institution."""
    institution, membership = _membership_admin_context(request, membership_id)

    if membership.status != MembershipStatus.APPROVED:
        messages.warning(request, 'Only an approved member can be suspended.')
    elif membership.user_id == request.user.id:
        messages.error(request, 'You cannot suspend your own account.')
    elif _last_active_admin_membership(institution, membership.id) and membership.role == Role.INSTITUTION_ADMIN:
        messages.error(
            request,
            'This is the only active administrator of '
            f'{institution.name}. Appoint another administrator first.',
        )
    else:
        with transaction.atomic():
            membership.status = MembershipStatus.SUSPENDED
            membership.reviewed_by = request.user
            membership.reviewed_at = timezone.now()
            membership.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
            record_audit(
                request=request,
                institution=institution,
                action=AuditLog.ActionChoices.MEMBERSHIP_SUSPENDED,
                description=(
                    f'Suspended {membership.user.email} '
                    f'({membership.get_role_display()}).'
                ),
            )
        messages.success(
            request,
            f'{membership.user.get_full_name() or membership.user.email} has '
            'been suspended and no longer has access.',
        )
    return redirect('accounts:membership_management')


@login_required
@role_required([Role.INSTITUTION_ADMIN])
@require_POST
def reactivate_membership_view(request, membership_id):
    """Restore a suspended member of the administrator's institution."""
    institution, membership = _membership_admin_context(request, membership_id)

    if membership.status != MembershipStatus.SUSPENDED:
        messages.warning(request, 'Only a suspended member can be reactivated.')
    else:
        with transaction.atomic():
            membership.status = MembershipStatus.APPROVED
            membership.reviewed_by = request.user
            membership.reviewed_at = timezone.now()
            membership.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
            record_audit(
                request=request,
                institution=institution,
                action=AuditLog.ActionChoices.MEMBERSHIP_REACTIVATED,
                description=(
                    f'Reactivated {membership.user.email} '
                    f'({membership.get_role_display()}).'
                ),
            )
        messages.success(
            request,
            f'{membership.user.get_full_name() or membership.user.email} has '
            'been reactivated.',
        )
    return redirect('accounts:membership_management')


@login_required
@role_required([Role.INSTITUTION_ADMIN])
@require_POST
def change_membership_role_view(request, membership_id):
    """Change the institutional role of an approved member."""
    institution, membership = _membership_admin_context(request, membership_id)

    new_role = request.POST.get('role', '').strip()
    if membership.status != MembershipStatus.APPROVED:
        messages.warning(request, 'Only an approved member can change role.')
    elif new_role not in dict(Role.choices):
        messages.error(request, 'Select a valid role.')
    elif new_role == membership.role:
        messages.info(request, 'The member already holds that role.')
    elif (
        membership.role == Role.INSTITUTION_ADMIN
        and _last_active_admin_membership(institution, membership.id)
    ):
        messages.error(
            request,
            'This is the only active administrator of '
            f'{institution.name}. Appoint another administrator first.',
        )
    else:
        previous_role = membership.role
        with transaction.atomic():
            membership.role = new_role
            membership.reviewed_by = request.user
            membership.reviewed_at = timezone.now()
            membership.save(update_fields=['role', 'reviewed_by', 'reviewed_at'])
            record_audit(
                request=request,
                institution=institution,
                action=AuditLog.ActionChoices.ROLE_CHANGED,
                description=(
                    f'Changed the role of {membership.user.email} from '
                    f'{previous_role} to {new_role}.'
                ),
            )
        messages.success(request, 'Role updated.')
    return redirect('accounts:membership_management')
