"""
Dashboard views - EduTrack

Role-specific dashboards. Every view is protected server-side with an explicit
role decorator and every query is constrained to the active institution and the
records the signed-in user is entitled to see.
"""

from statistics import mean

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import redirect, render

from accounts.decorators import (
    get_active_institution,
    get_active_membership,
    platform_admin_required,
    role_required,
)
from accounts.models import Membership, MembershipStatus, Role, User
from accounts.permissions import (
    instructor_offerings,
    user_is_platform_admin,
)
from academics.models import AcademicSession, CourseOffering, Enrollment, Semester
from advising.models import AcademicIntervention
from institutions.models import Institution
from monitoring.models import Assessment, AssessmentResult, AttendanceRecord
from predictions.models import RiskLevel, RiskPrediction

from . import analytics
from .models import AuditLog


def _page(request, queryset, per_page=20):
    return Paginator(queryset, per_page).get_page(request.GET.get('page'))


@login_required
def home_dispatch_view(request):
    """Send each user to the dashboard that matches their active role."""
    if user_is_platform_admin(request.user):
        return redirect('dashboard:platform_admin')

    membership = get_active_membership(request)
    if not membership:
        return redirect('accounts:pending_approval')

    routes = {
        Role.STUDENT: 'dashboard:student',
        Role.INSTRUCTOR: 'dashboard:instructor',
        Role.ADVISER: 'dashboard:adviser',
        Role.REGISTRAR: 'dashboard:registrar',
        Role.INSTITUTION_ADMIN: 'dashboard:institution_admin',
        Role.PLATFORM_ADMIN: 'dashboard:platform_admin',
    }
    return redirect(routes.get(membership.role, 'accounts:pending_approval'))


# ---------------------------------------------------------------------------
# Student
# ---------------------------------------------------------------------------

@login_required
@role_required([Role.STUDENT])
def student_dashboard_view(request):
    membership = get_active_membership(request)
    institution = membership.institution
    student = request.user

    current_semester = Semester.objects.filter(
        institution=institution, is_current=True
    ).first()

    # Course rows reflect the current semester; the trend charts still cover
    # every recorded semester.
    rows, attendance_overall = analytics.student_course_rows(
        student, institution, semester=current_semester
    )
    scored = [row['performance'] for row in rows if row['performance'] is not None]
    assessment_values = [
        row['assessment_score'] for row in rows
        if row['assessment_score'] is not None
    ]

    # Estimates are produced on demand by an authorised adviser or
    # administrator (POST). A plain GET of the dashboard never writes data.
    risk_prediction = RiskPrediction.objects.filter(
        student=student,
        institution=institution,
        is_current=True,
    ).select_related('semester').order_by('-predicted_at').first()

    interventions = AcademicIntervention.objects.filter(
        student=student,
        institution=institution,
    ).select_related('adviser').order_by('-date_conducted')[:5]

    context = {
        'membership': membership,
        'institution': institution,
        'student': student,
        'current_semester': current_semester,
        'course_rows': rows,
        'registered_courses': len(rows),
        'average_performance': round(mean(scored), 1) if scored else None,
        'assessment_average': round(mean(assessment_values), 1) if assessment_values else None,
        'overall_attendance': attendance_overall['rate'],
        'attendance_records': attendance_overall['total'],
        'recent_results': analytics.recent_results(student, institution, limit=6),
        'risk_prediction': risk_prediction,
        'interventions': interventions,
        # Distribution of the student's own recorded course scores, computed
        # from the rows already loaded above (no extra database access).
        'score_distribution': analytics.score_band_distribution(scored),
        'course_chart': {
            'labels': [row['course'].code for row in rows],
            'performance': [row['performance'] for row in rows],
            'attendance': [row['attendance_rate'] for row in rows],
        },
        'semester_trend': analytics.performance_by_semester(
            [student.id], institution
        ),
        'attendance_trend': analytics.attendance_by_semester(
            [student.id], institution
        ),
        'assessment_chart': analytics.assessment_progress(student, institution),
    }
    return render(request, 'dashboard/student.html', context)


@login_required
@role_required([Role.STUDENT])
def student_progress_view(request):
    institution = get_active_institution(request)
    progress = analytics.student_progress(request.user, institution)

    current_prediction = RiskPrediction.objects.filter(
        student=request.user,
        institution=institution,
        is_current=True,
    ).order_by('-predicted_at').first()

    return render(request, 'dashboard/student_progress.html', {
        'institution': institution,
        'progress': progress,
        'current_prediction': current_prediction,
    })


# ---------------------------------------------------------------------------
# Instructor / lecturer
# ---------------------------------------------------------------------------

def _instructor_context(request):
    institution = get_active_institution(request)
    data = analytics.instructor_analytics(request.user, institution)
    offerings = data['offerings']
    data['recent_assessments'] = (
        Assessment.objects
        .filter(institution=institution, course_offering__in=offerings)
        .select_related('course_offering__course')
        .order_by('-id')[:6]
    )
    data['institution'] = institution
    data['membership'] = get_active_membership(request)
    return data


@login_required
@role_required([Role.INSTRUCTOR])
def instructor_dashboard_view(request):
    return render(request, 'dashboard/instructor.html', _instructor_context(request))


@login_required
@role_required([Role.INSTRUCTOR])
def instructor_performance_view(request):
    return render(
        request,
        'dashboard/instructor_performance.html',
        _instructor_context(request),
    )


@login_required
@role_required([Role.INSTRUCTOR])
def instructor_students_view(request):
    institution = get_active_institution(request)
    offerings = instructor_offerings(request.user, institution, active_only=True)

    enrollments = (
        Enrollment.objects.filter(
            institution=institution,
            course_offering__in=offerings,
            status=Enrollment.EnrollmentStatus.ACTIVE,
        )
        .select_related('student', 'course_offering__course')
    )

    search = request.GET.get('q', '').strip()
    if search:
        enrollments = enrollments.filter(
            Q(student__first_name__icontains=search)
            | Q(student__last_name__icontains=search)
            | Q(student__email__icontains=search)
            | Q(student__matriculation_number__icontains=search)
        )

    student_ids = list(enrollments.values_list('student_id', flat=True).distinct())
    students = User.objects.filter(id__in=student_ids).order_by('last_name', 'first_name')
    page_obj = _page(request, students, per_page=25)

    course_map = {}
    for row in enrollments.values('student_id', 'course_offering__course__code'):
        course_map.setdefault(row['student_id'], set()).add(
            row['course_offering__course__code']
        )

    student_rows = [
        {'student': student, 'courses': sorted(course_map.get(student.id, []))}
        for student in page_obj
    ]

    return render(request, 'dashboard/instructor_students.html', {
        'institution': institution,
        'page_obj': page_obj,
        'student_rows': student_rows,
        'search': search,
        'total_students': len(student_ids),
    })


# ---------------------------------------------------------------------------
# Academic adviser
# ---------------------------------------------------------------------------

@login_required
@role_required([Role.ADVISER])
def adviser_dashboard_view(request):
    institution = get_active_institution(request)
    data = analytics.adviser_analytics(request.user, institution)
    data['institution'] = institution
    data['membership'] = get_active_membership(request)
    return render(request, 'dashboard/adviser.html', data)


@login_required
@role_required([Role.ADVISER])
def adviser_progress_view(request):
    institution = get_active_institution(request)
    data = analytics.adviser_analytics(request.user, institution)

    search = request.GET.get('q', '').strip()
    rows = data['student_rows']
    if search:
        term = search.lower()
        rows = [
            row for row in rows
            if term in (row['student'].get_full_name() or '').lower()
            or term in (row['student'].email or '').lower()
            or term in (row['student'].matriculation_number or '').lower()
        ]

    page_obj = _page(request, rows, per_page=20)
    return render(request, 'dashboard/adviser_progress.html', {
        'institution': institution,
        'page_obj': page_obj,
        'student_rows': page_obj.object_list,
        'search': search,
        'analytics': data,
    })


# ---------------------------------------------------------------------------
# Registrar
# ---------------------------------------------------------------------------

@login_required
@role_required([Role.REGISTRAR])
def registrar_dashboard_view(request):
    institution = get_active_institution(request)
    data = analytics.institution_analytics(institution)

    recent_enrollments = (
        Enrollment.objects.filter(institution=institution)
        .select_related('student', 'course_offering__course', 'course_offering__semester')
        .order_by('-enrolled_at')[:8]
    )
    active_offerings = (
        CourseOffering.objects.filter(institution=institution, is_active=True)
        .select_related('course', 'semester', 'instructor')
        .annotate(active_enrollments=Count(
            'enrollments',
            filter=Q(enrollments__status=Enrollment.EnrollmentStatus.ACTIVE),
        ))
        .order_by('course__code')[:8]
    )

    return render(request, 'dashboard/registrar.html', {
        'institution': institution,
        'membership': get_active_membership(request),
        **data,
        'recent_enrollments': recent_enrollments,
        'active_offerings': active_offerings,
        'session_count': AcademicSession.objects.filter(institution=institution).count(),
        'semester_count': Semester.objects.filter(institution=institution).count(),
    })


@login_required
@role_required([Role.REGISTRAR, Role.INSTITUTION_ADMIN])
def registrar_students_view(request):
    institution = get_active_institution(request)
    memberships = (
        Membership.objects.filter(
            institution=institution,
            role=Role.STUDENT,
            status=MembershipStatus.APPROVED,
        )
        .select_related('user', 'programme', 'department')
        .annotate(
            # Enrolment count computed by the database instead of a
            # Python-side dictionary built from a second full query.
            enrol_count=Count(
                'user__course_enrollments',
                filter=Q(
                    user__course_enrollments__institution=institution,
                ),
                distinct=True,
            ),
        )
        .order_by('user__last_name', 'user__first_name')
    )

    search = request.GET.get('q', '').strip()
    if search:
        memberships = memberships.filter(
            Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(user__email__icontains=search)
            | Q(user__matriculation_number__icontains=search)
        )

    page_obj = _page(request, memberships, per_page=25)
    student_rows = [
        {
            'membership': membership,
            'enrol_count': membership.enrol_count,
        }
        for membership in page_obj
    ]
    return render(request, 'dashboard/registrar_students.html', {
        'institution': institution,
        'page_obj': page_obj,
        'student_rows': student_rows,
        'search': search,
    })


# ---------------------------------------------------------------------------
# Institution administrator
# ---------------------------------------------------------------------------

@login_required
@role_required([Role.INSTITUTION_ADMIN])
def institution_admin_dashboard_view(request):
    institution = get_active_institution(request)
    data = analytics.institution_analytics(institution)

    pending_count = Membership.objects.filter(
        institution=institution, status=MembershipStatus.PENDING
    ).count()
    pending_memberships = (
        Membership.objects.filter(
            institution=institution, status=MembershipStatus.PENDING
        )
        .select_related('user', 'department')
        .order_by('-requested_at')[:5]
    )
    recent_logs = (
        AuditLog.objects.filter(institution=institution)
        .select_related('user')
        .order_by('-timestamp')[:8]
    )

    return render(request, 'dashboard/institution_admin.html', {
        'institution': institution,
        'membership': get_active_membership(request),
        **data,
        'pending_count': pending_count,
        'pending_memberships': pending_memberships,
        'recent_logs': recent_logs,
        'session_count': AcademicSession.objects.filter(institution=institution).count(),
        'semester_count': Semester.objects.filter(institution=institution).count(),
    })


@login_required
@role_required([Role.INSTITUTION_ADMIN])
def institution_reports_view(request):
    institution = get_active_institution(request)
    # The course chart in this report lists up to 30 offerings; passing the
    # limit here reuses one analytics pass instead of computing the same
    # aggregates twice.
    data = analytics.institution_analytics(institution, course_performance_limit=30)
    course_chart = data['course_performance']
    data['course_chart'] = course_chart
    data['course_table'] = [
        {'code': code, 'average': average, 'meta': meta}
        for code, average, meta in zip(
            course_chart['labels'],
            course_chart['values'],
            course_chart['meta'],
        )
    ]
    return render(request, 'dashboard/reports.html', {
        'institution': institution,
        'membership': get_active_membership(request),
        **data,
    })


@login_required
@role_required([Role.INSTITUTION_ADMIN])
def audit_logs_view(request):
    institution = get_active_institution(request)
    logs = (
        AuditLog.objects.filter(institution=institution)
        .select_related('user')
        .order_by('-timestamp')
    )

    action = request.GET.get('action', '').strip()
    if action in dict(AuditLog.ActionChoices.choices):
        logs = logs.filter(action=action)

    search = request.GET.get('q', '').strip()
    if search:
        logs = logs.filter(
            Q(description__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(user__email__icontains=search)
        )

    page_obj = _page(request, logs, per_page=30)
    return render(request, 'dashboard/audit_logs.html', {
        'institution': institution,
        'page_obj': page_obj,
        'logs': page_obj,
        'action_filter': action,
        'search': search,
        'action_choices': AuditLog.ActionChoices.choices,
    })


# ---------------------------------------------------------------------------
# Platform administrator
# ---------------------------------------------------------------------------

@login_required
@platform_admin_required
def platform_admin_dashboard_view(request):
    institutions = Institution.objects.all()
    active_institutions = institutions.filter(is_active=True)

    total_users = User.objects.filter(is_active=True).count()
    approved_memberships = Membership.objects.filter(
        status=MembershipStatus.APPROVED
    )
    role_counts = {
        row['role']: row['count']
        for row in approved_memberships.values('role').annotate(count=Count('id'))
    }

    activity_summary = [
        {
            'action': label,
            'count': AuditLog.objects.filter(action=value).count(),
        }
        for value, label in AuditLog.ActionChoices.choices
    ]

    return render(request, 'dashboard/platform_admin.html', {
        'total_institutions': institutions.count(),
        'active_institutions': active_institutions.count(),
        'pending_institutions': institutions.filter(is_active=False).count(),
        'total_users': total_users,
        'total_memberships': approved_memberships.count(),
        'role_counts': role_counts,
        'recent_institutions': institutions.order_by('-created_at')[:6],
        'activity_summary': activity_summary,
        'recent_activity': (
            AuditLog.objects.select_related('institution')
            .order_by('-timestamp')[:12]
        ),
    })


@login_required
@platform_admin_required
def platform_users_view(request):
    users = User.objects.prefetch_related(
        'memberships__institution'
    ).order_by('last_name', 'first_name')

    search = request.GET.get('q', '').strip()
    if search:
        users = users.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
        )

    role = request.GET.get('role', '').strip()
    if role in dict(Role.choices):
        users = users.filter(memberships__role=role)

    page_obj = _page(request, users.distinct(), per_page=25)
    return render(request, 'dashboard/platform_users.html', {
        'page_obj': page_obj,
        'users': page_obj,
        'search': search,
        'role_filter': role,
        'roles': Role.choices,
    })


@login_required
@platform_admin_required
def platform_activity_view(request):
    logs = AuditLog.objects.select_related(
        'institution', 'user'
    ).order_by('-timestamp')

    action = request.GET.get('action', '').strip()
    if action in dict(AuditLog.ActionChoices.choices):
        logs = logs.filter(action=action)

    page_obj = _page(request, logs, per_page=30)
    summary = [
        {
            'label': label,
            'value': value,
            'count': AuditLog.objects.filter(action=value).count(),
        }
        for value, label in AuditLog.ActionChoices.choices
    ]

    return render(request, 'dashboard/platform_activity.html', {
        'page_obj': page_obj,
        'logs': page_obj,
        'action_filter': action,
        'action_choices': AuditLog.ActionChoices.choices,
        'summary': summary,
    })
