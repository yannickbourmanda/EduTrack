"""
Institutions views - EduTrack

Institutional structure for authorised staff, and platform-wide institution
management for platform administrators only.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

import re

HEX_COLOUR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')

from accounts.decorators import (
    get_active_institution,
    platform_admin_required,
    role_required,
)
from accounts.models import Membership, MembershipStatus, Role, User
from dashboard.models import AuditLog, record_audit

from .models import Department, Faculty, Institution, Programme


@login_required
@role_required([Role.INSTITUTION_ADMIN, Role.REGISTRAR])
def structure_view(request):
    """Academic hierarchy for the active institution only."""
    institution = get_active_institution(request)

    faculties = (
        Faculty.objects.filter(institution=institution)
        .prefetch_related('departments__programmes')
    )

    return render(request, 'institutions/structure.html', {
        'faculties': faculties,
        'institution': institution,
    })


@login_required
@platform_admin_required
def institution_list_view(request):
    institutions = Institution.objects.annotate(
        member_count=Count(
            'memberships',
            filter=Q(memberships__status=MembershipStatus.APPROVED),
        ),
        admin_count=Count(
            'memberships',
            filter=Q(
                memberships__role=Role.INSTITUTION_ADMIN,
                memberships__status=MembershipStatus.APPROVED,
            ),
        ),
    ).order_by('name')

    search = request.GET.get('q', '').strip()
    if search:
        institutions = institutions.filter(
            Q(name__icontains=search)
            | Q(code__icontains=search)
            | Q(contact_email__icontains=search)
        )

    status = request.GET.get('status', '').strip()
    if status == 'active':
        institutions = institutions.filter(is_active=True)
    elif status == 'pending':
        institutions = institutions.filter(is_active=False)

    page_obj = Paginator(institutions, 20).get_page(request.GET.get('page'))
    return render(request, 'institutions/institution_list.html', {
        'institutions': page_obj,
        'page_obj': page_obj,
        'search': search,
        'status_filter': status,
    })


@login_required
@platform_admin_required
def institution_requests_view(request):
    """Inactive institutions awaiting activation by the platform."""
    requests_qs = (
        Institution.objects.filter(is_active=False)
        .annotate(
            member_count=Count(
                'memberships',
                filter=Q(memberships__status=MembershipStatus.APPROVED),
            )
        )
        .order_by('name')
    )
    page_obj = Paginator(requests_qs, 20).get_page(request.GET.get('page'))
    return render(request, 'institutions/institution_requests.html', {
        'institutions': page_obj,
        'page_obj': page_obj,
    })


@login_required
@platform_admin_required
def institution_create_view(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        contact_email = request.POST.get('contact_email', '').strip()
        contact_phone = request.POST.get('contact_phone', '').strip()
        website = request.POST.get('website', '').strip()
        address = request.POST.get('address', '').strip()
        brand_primary = request.POST.get('brand_primary_color', '').strip()
        brand_accent = request.POST.get('brand_accent_color', '').strip()
        logo_url = request.POST.get('logo_url', '').strip()

        errors = []
        if not name:
            errors.append('Institution name is required.')
        if not code:
            errors.append('Institution code is required.')
        if not contact_email:
            errors.append('A contact email is required.')
        for label, value in (
            ('primary brand colour', brand_primary),
            ('accent colour', brand_accent),
        ):
            if value and not HEX_COLOUR_RE.match(value):
                errors.append(f'{label.capitalize()} must use #RRGGBB format.')
        if logo_url and not logo_url.lower().startswith(('http://', 'https://')):
            errors.append('The logo URL must start with http:// or https://.')

        slug = slugify(code or name)
        if not slug:
            errors.append('A valid institution code is required.')
        elif Institution.objects.filter(slug=slug).exists():
            errors.append('An institution with this code already exists.')
        if Institution.objects.filter(name__iexact=name).exists() and name:
            errors.append('An institution with this name already exists.')

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            with transaction.atomic():
                institution = Institution.objects.create(
                    name=name,
                    code=code,
                    slug=slug,
                    contact_email=contact_email,
                    contact_phone=contact_phone,
                    website=website,
                    address=address,
                    brand_primary_color=brand_primary.upper(),
                    brand_accent_color=brand_accent.upper(),
                    logo_url=logo_url,
                )
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.INSTITUTION_REGISTERED,
                    description=(
                        f'Institution {name} ({code}) registered on the '
                        'platform.'
                    ),
                )
            messages.success(request, f'Institution {name} registered.')
            return redirect('institutions:institution_list')

    return render(request, 'institutions/institution_form.html')


@login_required
@platform_admin_required
@require_POST
def institution_toggle_view(request, institution_id):
    institution = get_object_or_404(Institution, id=institution_id)
    new_status = not institution.is_active

    with transaction.atomic():
        institution.is_active = new_status
        institution.save(update_fields=['is_active', 'updated_at'])
        record_audit(
            request=request,
            institution=institution,
            action=(
                AuditLog.ActionChoices.INSTITUTION_ACTIVATED
                if new_status else AuditLog.ActionChoices.INSTITUTION_SUSPENDED
            ),
            description=(
                f'Institution {institution.name} was '
                f'{"activated" if new_status else "suspended"} '
                'by the platform administrator.'
            ),
        )

    messages.success(
        request,
        f'{institution.name} is now '
        f'{"active" if new_status else "suspended"}.'
    )
    next_url = request.POST.get('next', '')
    allowed = {
        '/institutions/': 'institutions:institution_list',
        '/institutions/requests/': 'institutions:institution_requests',
    }
    return redirect(allowed.get(next_url, 'institutions:institution_list'))


@login_required
@platform_admin_required
def institution_admin_form_view(request, institution_id):
    """
    Onboard the FIRST administrator of a newly created institution.

    A new tenant cannot bootstrap itself: membership approval requires an
    existing institution administrator, and platform administrators are
    deliberately excluded from institutional academic records. This view
    gives the platform a POST-only, audited way to appoint the first
    administrator, without granting any academic-record access.
    """
    institution = get_object_or_404(Institution, id=institution_id)

    if Membership.objects.filter(
        institution=institution,
        role=Role.INSTITUTION_ADMIN,
        status=MembershipStatus.APPROVED,
    ).exists():
        messages.error(
            request,
            f'{institution.name} already has an active administrator. '
            'Role changes are handled inside the institution.',
        )
        return redirect('institutions:institution_list')

    users = User.objects.filter(is_active=True).order_by('email')

    if request.method == 'POST':
        user_id = request.POST.get('user', '').strip()
        candidate = None
        if user_id.isdigit():
            candidate = users.filter(id=int(user_id)).first()
        if candidate is None:
            messages.error(request, 'Select an active user to appoint.')
        else:
            with transaction.atomic():
                membership = Membership.objects.filter(
                    user=candidate, institution=institution
                ).first()
                if membership is None:
                    membership = Membership.objects.create(
                        user=candidate,
                        institution=institution,
                        role=Role.INSTITUTION_ADMIN,
                        status=MembershipStatus.APPROVED,
                        reviewed_by=request.user,
                        reviewed_at=timezone.now(),
                    )
                else:
                    membership.role = Role.INSTITUTION_ADMIN
                    membership.approve(request.user)
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.INSTITUTION_ADMIN_APPOINTED,
                    description=(
                        f'Appointed {candidate.email} as the first institution '
                        f'administrator of {institution.name} ({institution.code}).'
                    ),
                )
            messages.success(
                request,
                f'{candidate.get_full_name() or candidate.email} is now the '
                f'administrator of {institution.name}. They can sign in and '
                'approve the remaining members.',
            )
            return redirect(
                allowed_next(request)
            )

    return render(request, 'institutions/institution_admin_form.html', {
        'institution': institution,
        'users': users,
    })


def allowed_next(request):
    """Allow-list for platform post-action redirects."""
    next_url = request.POST.get('next', '')
    return {
        '/institutions/': 'institutions:institution_list',
        '/institutions/requests/': 'institutions:institution_requests',
    }.get(next_url, 'institutions:institution_list')


@login_required
@role_required([Role.INSTITUTION_ADMIN, Role.REGISTRAR])
def structure_create_view(request):
    """
    Create the academic structure of the active institution: faculty,
    department within a faculty, or programme within a department.

    A new tenant has none of these, so without this form its administrator
    could never add courses (a course requires a department).
    """
    institution = get_active_institution(request)
    faculties = Faculty.objects.filter(institution=institution).order_by('name')
    departments = (
        Department.objects.filter(institution=institution)
        .select_related('faculty')
        .order_by('name')
    )

    if request.method == 'POST':
        kind = request.POST.get('kind', '').strip()
        errors = []

        if kind == 'faculty':
            name = request.POST.get('name', '').strip()
            code = request.POST.get('code', '').strip().upper()
            if not name or not code:
                errors.append('Faculty name and code are required.')
            elif Faculty.objects.filter(institution=institution, code=code).exists():
                errors.append(f'Faculty code {code} already exists.')
            if not errors:
                faculty = Faculty.objects.create(
                    institution=institution, name=name, code=code
                )
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.STRUCTURE_CREATED,
                    description=f'Created faculty {name} ({code}).',
                )
                messages.success(request, f'Faculty {name} created.')
                return redirect('institutions:structure_view')

        elif kind == 'department':
            name = request.POST.get('name', '').strip()
            code = request.POST.get('code', '').strip().upper()
            faculty_id = request.POST.get('faculty', '').strip()
            faculty = (
                faculties.filter(id=faculty_id).first()
                if faculty_id.isdigit() else None
            )
            if not name or not code:
                errors.append('Department name and code are required.')
            elif faculty is None:
                errors.append('Select a faculty of this institution.')
            elif Department.objects.filter(institution=institution, code=code).exists():
                errors.append(f'Department code {code} already exists.')
            if not errors:
                Department.objects.create(
                    institution=institution,
                    faculty=faculty,
                    name=name,
                    code=code,
                )
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.STRUCTURE_CREATED,
                    description=(
                        f'Created department {name} ({code}) in '
                        f'{faculty.name}.'
                    ),
                )
                messages.success(request, f'Department {name} created.')
                return redirect('institutions:structure_view')

        elif kind == 'programme':
            name = request.POST.get('name', '').strip()
            code = request.POST.get('code', '').strip().upper()
            degree_type = request.POST.get('degree_type', 'BSC').strip()
            duration_raw = request.POST.get('duration_years', '4').strip()
            department_id = request.POST.get('department', '').strip()
            department = (
                departments.filter(id=department_id).first()
                if department_id.isdigit() else None
            )
            try:
                duration_years = int(duration_raw)
            except ValueError:
                duration_years = 0
            if not name or not code:
                errors.append('Programme name and code are required.')
            elif department is None:
                errors.append('Select a department of this institution.')
            elif degree_type not in dict(Programme.DEGREE_TYPES):
                errors.append('Select a valid degree type.')
            elif duration_years < 1 or duration_years > 10:
                errors.append('Duration must be between 1 and 10 years.')
            elif Programme.objects.filter(institution=institution, code=code).exists():
                errors.append(f'Programme code {code} already exists.')
            if not errors:
                Programme.objects.create(
                    institution=institution,
                    department=department,
                    name=name,
                    code=code,
                    degree_type=degree_type,
                    duration_years=duration_years,
                )
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.STRUCTURE_CREATED,
                    description=(
                        f'Created programme {name} ({code}) in '
                        f'{department.name}.'
                    ),
                )
                messages.success(request, f'Programme {name} created.')
                return redirect('institutions:structure_view')

        else:
            errors.append('Choose what to create.')

        for error in errors:
            messages.error(request, error)
        return redirect('institutions:structure_create')

    return render(request, 'institutions/structure_form.html', {
        'institution': institution,
        'faculties': faculties,
        'departments': departments,
        'degree_types': Programme.DEGREE_TYPES,
    })


@login_required
@role_required([Role.INSTITUTION_ADMIN, Role.REGISTRAR])
def departments_view(request):
    """
    Dedicated Departments page for the active institution.

    Replaces the former fragment link to the structure overview: the sidebar
    entry now opens a real page with search, pagination and its own empty
    state. Access stays limited to institution administrators and registrars,
    exactly like the structure views it complements.
    """
    institution = get_active_institution(request)

    departments = (
        Department.objects.filter(institution=institution)
        .select_related('faculty')
        .annotate(
            programme_count=Count('programmes', distinct=True),
            course_count=Count('courses', distinct=True),
        )
        .order_by('name')
    )

    search = request.GET.get('q', '').strip()
    if search:
        departments = departments.filter(
            Q(name__icontains=search)
            | Q(code__icontains=search)
            | Q(faculty__name__icontains=search)
        )

    page_obj = Paginator(departments, 20).get_page(request.GET.get('page'))
    return render(request, 'institutions/departments.html', {
        'institution': institution,
        'page_obj': page_obj,
        'departments': page_obj,
        'search': search,
        'faculty_count': Faculty.objects.filter(institution=institution).count(),
    })


@login_required
@role_required([Role.INSTITUTION_ADMIN, Role.REGISTRAR])
def programmes_view(request):
    """Dedicated Programmes page for the active institution."""
    institution = get_active_institution(request)

    programmes = (
        Programme.objects.filter(institution=institution)
        .select_related('department__faculty')
        .order_by('name')
    )

    search = request.GET.get('q', '').strip()
    if search:
        programmes = programmes.filter(
            Q(name__icontains=search)
            | Q(code__icontains=search)
            | Q(department__name__icontains=search)
        )

    page_obj = Paginator(programmes, 20).get_page(request.GET.get('page'))
    return render(request, 'institutions/programmes.html', {
        'institution': institution,
        'page_obj': page_obj,
        'programmes': page_obj,
        'search': search,
        'department_count': Department.objects.filter(
            institution=institution
        ).count(),
    })


@login_required
@platform_admin_required
def institution_branding_view(request, institution_id):
    """
    Edit the branding of one institution (platform administrators only).

    Branding is presentation-only configuration: colours are validated as
    #RRGGBB, the logo is a plain URL or the static naming convention, and the
    change is audited. Institutional academic data is never touched here.
    """
    institution = get_object_or_404(Institution, id=institution_id)

    if request.method == 'POST':
        brand_primary = request.POST.get('brand_primary_color', '').strip()
        brand_accent = request.POST.get('brand_accent_color', '').strip()
        logo_url = request.POST.get('logo_url', '').strip()

        errors = []
        if brand_primary and not HEX_COLOUR_RE.match(brand_primary):
            errors.append('Primary brand colour must use #RRGGBB format.')
        if brand_accent and not HEX_COLOUR_RE.match(brand_accent):
            errors.append('Accent colour must use #RRGGBB format.')
        if logo_url and not logo_url.lower().startswith(('http://', 'https://')):
            errors.append('The logo URL must start with http:// or https://.')

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            institution.brand_primary_color = brand_primary.upper()
            institution.brand_accent_color = brand_accent.upper()
            institution.logo_url = logo_url
            institution.save(update_fields=[
                'brand_primary_color',
                'brand_accent_color',
                'logo_url',
                'updated_at',
            ])
            record_audit(
                request=request,
                institution=institution,
                action=AuditLog.ActionChoices.INSTITUTION_BRANDING_UPDATED,
                description=(
                    f'Branding updated for {institution.name} '
                    f'({institution.code}).'
                ),
            )
            messages.success(
                request,
                f'Branding for {institution.name} saved.',
            )
            return redirect('institutions:institution_list')

    return render(request, 'institutions/institution_branding_form.html', {
        'institution': institution,
    })
