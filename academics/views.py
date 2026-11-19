"""
Academics views - EduTrack

Course catalogue, semester offerings, student enrolment, academic sessions and
registrar results. Every query is scoped to the active institution; platform
administrators cannot read institutional academic records.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import get_active_institution, get_active_membership, role_required
from accounts.models import Membership, MembershipStatus, Role, User
from accounts.permissions import instructor_offerings
from dashboard import analytics
from dashboard.models import AuditLog, record_audit

from .models import AcademicSession, Course, CourseOffering, Enrollment, Semester


def _page(request, queryset, per_page=25):
    return Paginator(queryset, per_page).get_page(request.GET.get('page'))


@login_required
@role_required([Role.STUDENT])
def my_courses_view(request):
    """
    A student's own active course registrations.

    Rendered as a responsive card grid; search, semester filter and
    pagination keep the page usable when a student carries many courses in a
    semester. The queryset is scoped to the signed-in student and the active
    institution; no other student's registrations can be listed.
    """
    institution = get_active_institution(request)
    enrollments = (
        Enrollment.objects.filter(
            student=request.user,
            institution=institution,
            status=Enrollment.EnrollmentStatus.ACTIVE,
        )
        .select_related(
            'course_offering__course',
            'course_offering__instructor',
            'course_offering__semester__session',
        )
        .order_by(
            '-course_offering__semester__start_date',
            'course_offering__course__code',
        )
    )

    # Semester options come from the student's own registrations only.
    semester_ids = set(
        Enrollment.objects.filter(
            student=request.user,
            institution=institution,
        ).values_list('course_offering__semester_id', flat=True)
    )
    semesters = Semester.objects.filter(
        id__in=semester_ids,
        institution=institution,
    ).select_related('session').order_by('-start_date', 'name')

    search = request.GET.get('q', '').strip()
    if search:
        enrollments = enrollments.filter(
            Q(course_offering__course__code__icontains=search)
            | Q(course_offering__course__title__icontains=search)
            | Q(course_offering__instructor__first_name__icontains=search)
            | Q(course_offering__instructor__last_name__icontains=search)
        )

    semester_filter = request.GET.get('semester', '').strip()
    if semester_filter.isdigit():
        enrollments = enrollments.filter(
            course_offering__semester_id=int(semester_filter)
        )

    page_obj = _page(request, enrollments, per_page=24)
    return render(request, 'academics/my_courses.html', {
        'enrollments': page_obj,
        'page_obj': page_obj,
        'institution': institution,
        'semesters': semesters,
        'semester_filter': semester_filter,
        'search': search,
    })


@login_required
@role_required([Role.INSTRUCTOR])
def instructor_courses_view(request):
    """
    Only the offerings assigned to the signed-in instructor.

    Same responsive card treatment as the student course list: search,
    semester filter and pagination keep large teaching loads readable.
    """
    institution = get_active_institution(request)
    offerings = (
        instructor_offerings(request.user, institution, active_only=True)
        .select_related('course', 'session', 'semester')
        .annotate(active_enrollments=Count(
            'enrollments',
            filter=Q(enrollments__status=Enrollment.EnrollmentStatus.ACTIVE),
        ))
        .order_by(
            '-semester__start_date',
            'course__code',
        )
    )

    semester_ids = set(
        instructor_offerings(request.user, institution)
        .values_list('semester_id', flat=True)
    )
    semesters = Semester.objects.filter(
        id__in=semester_ids,
        institution=institution,
    ).select_related('session').order_by('-start_date', 'name')

    search = request.GET.get('q', '').strip()
    if search:
        offerings = offerings.filter(
            Q(course__code__icontains=search)
            | Q(course__title__icontains=search)
        )

    semester_filter = request.GET.get('semester', '').strip()
    if semester_filter.isdigit():
        offerings = offerings.filter(semester_id=int(semester_filter))

    page_obj = _page(request, offerings, per_page=24)
    return render(request, 'academics/instructor_courses.html', {
        'offerings': page_obj,
        'page_obj': page_obj,
        'institution': institution,
        'semesters': semesters,
        'semester_filter': semester_filter,
        'search': search,
    })


@login_required
@role_required([Role.REGISTRAR, Role.INSTITUTION_ADMIN])
def course_list_view(request):
    institution = get_active_institution(request)
    courses = (
        Course.objects.filter(institution=institution)
        .select_related('department__faculty')
        .order_by('code')
    )

    search = request.GET.get('q', '').strip()
    if search:
        courses = courses.filter(
            Q(code__icontains=search)
            | Q(title__icontains=search)
            | Q(department__name__icontains=search)
        )

    department = request.GET.get('department', '').strip()
    if department.isdigit():
        courses = courses.filter(department_id=department)

    page_obj = _page(request, courses)
    return render(request, 'academics/course_list.html', {
        'courses': page_obj,
        'page_obj': page_obj,
        'institution': institution,
        'search': search,
        'department_filter': department,
        'departments': institution.institutions_department_set.all().order_by('name'),
    })


@login_required
@role_required([Role.REGISTRAR, Role.INSTITUTION_ADMIN])
def course_create_view(request):
    institution = get_active_institution(request)
    departments = institution.institutions_department_set.all().order_by('name')

    if request.method == 'POST':
        dept_id = request.POST.get('department', '').strip()
        code = request.POST.get('code', '').strip().upper()
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        credits_raw = request.POST.get('credit_units', '3').strip()

        try:
            credits = int(credits_raw)
        except ValueError:
            credits = 0

        if not code or not title:
            messages.error(request, 'Course code and title are required.')
        elif credits < 1 or credits > 12:
            messages.error(request, 'Credit units must be between 1 and 12.')
        else:
            from institutions.models import Department

            department = Department.objects.filter(
                id=dept_id, institution=institution
            ).first() if dept_id.isdigit() else None

            if department is None:
                messages.error(request, 'Select a valid department.')
            elif Course.objects.filter(institution=institution, code=code).exists():
                messages.error(request, f'Course code {code} already exists.')
            else:
                with transaction.atomic():
                    Course.objects.create(
                        institution=institution,
                        department=department,
                        code=code,
                        title=title,
                        credit_units=credits,
                        description=description,
                    )
                    record_audit(
                        request=request,
                        institution=institution,
                        action=AuditLog.ActionChoices.COURSE_CREATED,
                        description=f'Added course {code} ({title}) to catalogue.',
                    )
                messages.success(request, f'Course {code} added successfully.')
                return redirect('academics:course_list')

    return render(request, 'academics/course_form.html', {
        'departments': departments,
        'institution': institution,
    })


@login_required
@role_required([Role.REGISTRAR, Role.INSTITUTION_ADMIN])
def offering_list_view(request):
    institution = get_active_institution(request)
    offerings = (
        CourseOffering.objects.filter(institution=institution)
        .select_related('course', 'session', 'semester', 'instructor')
        .annotate(active_enrollments=Count(
            'enrollments',
            filter=Q(enrollments__status=Enrollment.EnrollmentStatus.ACTIVE),
        ))
        .order_by('course__code')
    )

    search = request.GET.get('q', '').strip()
    if search:
        offerings = offerings.filter(
            Q(course__code__icontains=search)
            | Q(course__title__icontains=search)
            | Q(instructor__first_name__icontains=search)
            | Q(instructor__last_name__icontains=search)
        )

    semester = request.GET.get('semester', '').strip()
    if semester.isdigit():
        offerings = offerings.filter(semester_id=semester)

    page_obj = _page(request, offerings)
    return render(request, 'academics/offering_list.html', {
        'offerings': page_obj,
        'page_obj': page_obj,
        'institution': institution,
        'search': search,
        'semester_filter': semester,
        'semesters': Semester.objects.filter(
            institution=institution
        ).select_related('session').order_by('-start_date'),
    })


@login_required
@role_required([Role.REGISTRAR, Role.INSTITUTION_ADMIN])
def enrollment_list_view(request):
    institution = get_active_institution(request)
    enrollments = (
        Enrollment.objects.filter(institution=institution)
        .select_related(
            'student',
            'course_offering__course',
            'course_offering__semester__session',
        )
        .order_by('student__last_name', 'student__first_name', 'course_offering__course__code')
    )

    search = request.GET.get('q', '').strip()
    if search:
        enrollments = enrollments.filter(
            Q(student__first_name__icontains=search)
            | Q(student__last_name__icontains=search)
            | Q(student__email__icontains=search)
            | Q(student__matriculation_number__icontains=search)
            | Q(course_offering__course__code__icontains=search)
        )

    status = request.GET.get('status', '').strip()
    if status in dict(Enrollment.EnrollmentStatus.choices):
        enrollments = enrollments.filter(status=status)

    semester = request.GET.get('semester', '').strip()
    if semester.isdigit():
        enrollments = enrollments.filter(course_offering__semester_id=semester)

    page_obj = _page(request, enrollments)
    page_rows = list(page_obj.object_list)
    student_ids = list({row.student_id for row in page_rows})
    performance_map = analytics.assessment_performance_map(institution, student_ids)

    enrollment_rows = [
        {
            'enrollment': row,
            'performance': analytics.enrollment_performance(row, performance_map),
        }
        for row in page_rows
    ]

    return render(request, 'academics/enrollment_list.html', {
        'institution': institution,
        'page_obj': page_obj,
        'enrollment_rows': enrollment_rows,
        'search': search,
        'status_filter': status,
        'semester_filter': semester,
        'status_choices': Enrollment.EnrollmentStatus.choices,
        'semesters': Semester.objects.filter(
            institution=institution
        ).select_related('session').order_by('-start_date'),
    })


@login_required
@role_required([Role.REGISTRAR, Role.INSTITUTION_ADMIN])
def session_list_view(request):
    institution = get_active_institution(request)
    sessions = (
        AcademicSession.objects.filter(institution=institution)
        .prefetch_related('semesters')
        .order_by('-start_date')
    )

    search = request.GET.get('q', '').strip()
    if search:
        sessions = sessions.filter(name__icontains=search)

    page_obj = _page(request, sessions, per_page=10)
    return render(request, 'academics/session_list.html', {
        'institution': institution,
        'page_obj': page_obj,
        'sessions': page_obj,
        'search': search,
    })


@login_required
@role_required([Role.REGISTRAR, Role.INSTITUTION_ADMIN])
def registrar_results_view(request):
    """Recorded final results and live assessment averages for processing."""
    institution = get_active_institution(request)
    enrollments = (
        Enrollment.objects.filter(institution=institution)
        .select_related(
            'student',
            'course_offering__course',
            'course_offering__semester__session',
        )
        .order_by(
            '-course_offering__semester__start_date',
            'student__last_name',
            'student__first_name',
        )
    )

    search = request.GET.get('q', '').strip()
    if search:
        enrollments = enrollments.filter(
            Q(student__first_name__icontains=search)
            | Q(student__last_name__icontains=search)
            | Q(student__matriculation_number__icontains=search)
            | Q(course_offering__course__code__icontains=search)
        )

    semester = request.GET.get('semester', '').strip()
    if semester.isdigit():
        enrollments = enrollments.filter(course_offering__semester_id=semester)

    only_final = request.GET.get('final') == '1'
    if only_final:
        enrollments = enrollments.filter(final_score__isnull=False)

    page_obj = _page(request, enrollments, per_page=30)
    page_rows = list(page_obj.object_list)
    student_ids = list({row.student_id for row in page_rows})
    performance_map = analytics.assessment_performance_map(institution, student_ids)

    result_rows = [
        {
            'enrollment': row,
            'performance': analytics.enrollment_performance(row, performance_map),
            'assessment_average': performance_map.get(
                (row.course_offering_id, row.student_id)
            ),
        }
        for row in page_rows
    ]

    return render(request, 'academics/registrar_results.html', {
        'institution': institution,
        'page_obj': page_obj,
        'result_rows': result_rows,
        'search': search,
        'semester_filter': semester,
        'only_final': only_final,
        'semesters': Semester.objects.filter(
            institution=institution
        ).select_related('session').order_by('-start_date'),
    })


# ---------------------------------------------------------------------------
# Registrar workflows: sessions, semesters, offerings, enrolment, results
# ---------------------------------------------------------------------------

def _parse_date(raw):
    try:
        from datetime import date as date_cls
        return date_cls.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


@login_required
@role_required([Role.REGISTRAR, Role.INSTITUTION_ADMIN])
def session_create_view(request):
    """Create an academic session for the active institution."""
    institution = get_active_institution(request)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()[:50]
        start_date = _parse_date(request.POST.get('start_date', ''))
        end_date = _parse_date(request.POST.get('end_date', ''))
        is_current = request.POST.get('is_current') == 'on'

        errors = []
        if not name:
            errors.append('Session name is required (e.g. 2026/2027).')
        elif AcademicSession.objects.filter(institution=institution, name=name).exists():
            errors.append('A session with this name already exists.')
        if start_date is None or end_date is None:
            errors.append('Start and end dates are required.')
        elif start_date >= end_date:
            errors.append('The end date must be after the start date.')

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            with transaction.atomic():
                AcademicSession.objects.create(
                    institution=institution,
                    name=name,
                    start_date=start_date,
                    end_date=end_date,
                    is_current=is_current,
                )
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.SESSION_CREATED,
                    description=(
                        f'Created academic session {name} '
                        f'({start_date.isoformat()} to {end_date.isoformat()}).'
                    ),
                )
            messages.success(request, f'Session {name} created.')
            return redirect('academics:session_list')

    return render(request, 'academics/session_form.html', {
        'institution': institution,
    })


@login_required
@role_required([Role.REGISTRAR, Role.INSTITUTION_ADMIN])
def semester_create_view(request):
    """Create a semester within one of the institution's sessions."""
    institution = get_active_institution(request)
    sessions = AcademicSession.objects.filter(institution=institution).order_by('-start_date')

    if request.method == 'POST':
        session_id = request.POST.get('session', '').strip()
        name = request.POST.get('name', '').strip()[:100]
        term = request.POST.get('term', Semester.TermChoices.FIRST).strip()
        start_date = _parse_date(request.POST.get('start_date', ''))
        end_date = _parse_date(request.POST.get('end_date', ''))
        is_current = request.POST.get('is_current') == 'on'

        parent_session = (
            sessions.filter(id=session_id).first()
            if session_id.isdigit() else None
        )

        errors = []
        if parent_session is None:
            errors.append('Select an academic session of your institution.')
        if not name:
            errors.append('Semester name is required.')
        if term not in dict(Semester.TermChoices.choices):
            errors.append('Select a valid term.')
        elif parent_session is not None and Semester.objects.filter(
            institution=institution, session=parent_session, term=term
        ).exists():
            errors.append('That session already has this term.')
        if start_date is None or end_date is None:
            errors.append('Start and end dates are required.')
        elif start_date >= end_date:
            errors.append('The end date must be after the start date.')

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            with transaction.atomic():
                Semester.objects.create(
                    institution=institution,
                    session=parent_session,
                    name=name,
                    term=term,
                    start_date=start_date,
                    end_date=end_date,
                    is_current=is_current,
                )
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.SEMESTER_CREATED,
                    description=(
                        f'Created semester {name} ({term}) in '
                        f'{parent_session.name}.'
                    ),
                )
            messages.success(request, f'Semester {name} created.')
            return redirect('academics:session_list')

    return render(request, 'academics/semester_form.html', {
        'institution': institution,
        'sessions': sessions,
        'terms': Semester.TermChoices.choices,
    })


@login_required
@role_required([Role.REGISTRAR, Role.INSTITUTION_ADMIN])
def offering_create_view(request):
    """Create a course offering, assigning an instructor of the institution."""
    institution = get_active_institution(request)
    courses = Course.objects.filter(institution=institution).order_by('code')
    semesters = (
        Semester.objects.filter(institution=institution)
        .select_related('session')
        .order_by('-start_date')
    )
    instructors = User.objects.filter(
        memberships__institution=institution,
        memberships__role=Role.INSTRUCTOR,
        memberships__status=MembershipStatus.APPROVED,
        is_active=True,
    ).distinct().order_by('last_name', 'first_name')

    if request.method == 'POST':
        course_id = request.POST.get('course', '').strip()
        semester_id = request.POST.get('semester', '').strip()
        instructor_id = request.POST.get('instructor', '').strip()
        syllabus = request.POST.get('syllabus', '').strip()[:5000]
        is_active = request.POST.get('is_active', 'on') == 'on'

        course = courses.filter(id=course_id).first() if course_id.isdigit() else None
        semester = semesters.filter(id=semester_id).first() if semester_id.isdigit() else None
        instructor = (
            instructors.filter(id=instructor_id).first()
            if instructor_id.isdigit() else None
        )

        errors = []
        if course is None:
            errors.append('Select a course from the catalogue.')
        if semester is None:
            errors.append('Select a semester of your institution.')
        if instructor is None:
            errors.append('Select an instructor who holds an approved '
                          'membership at your institution.')
        elif course is not None and semester is not None and CourseOffering.objects.filter(
            institution=institution, course=course, semester=semester
        ).exists():
            errors.append(
                f'{course.code} is already offered in '
                f'{semester.name} ({semester.session.name}).'
            )

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            with transaction.atomic():
                offering = CourseOffering.objects.create(
                    institution=institution,
                    course=course,
                    session=semester.session,
                    semester=semester,
                    instructor=instructor,
                    syllabus=syllabus,
                    is_active=is_active,
                )
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.OFFERING_CREATED,
                    description=(
                        f'Created offering {course.code} in '
                        f'{semester.name} ({semester.session.name}) taught by '
                        f'{instructor.get_full_name() or instructor.email}.'
                    ),
                )
            messages.success(request, f'Offering for {course.code} created.')
            return redirect('academics:offering_list')

    return render(request, 'academics/offering_form.html', {
        'institution': institution,
        'courses': courses,
        'semesters': semesters,
        'instructors': instructors,
    })


@login_required
@role_required([Role.REGISTRAR, Role.INSTITUTION_ADMIN])
def enrollment_create_view(request):
    """Enrol a student of the institution into an active offering."""
    institution = get_active_institution(request)
    students = User.objects.filter(
        memberships__institution=institution,
        memberships__role=Role.STUDENT,
        memberships__status=MembershipStatus.APPROVED,
        is_active=True,
    ).distinct().order_by('last_name', 'first_name')
    offerings = (
        CourseOffering.objects.filter(institution=institution, is_active=True)
        .select_related('course', 'semester__session')
        .order_by('course__code')
    )

    if request.method == 'POST':
        student_id = request.POST.get('student', '').strip()
        offering_id = request.POST.get('course_offering', '').strip()

        student = students.filter(id=student_id).first() if student_id.isdigit() else None
        offering = offerings.filter(id=offering_id).first() if offering_id.isdigit() else None

        errors = []
        if student is None:
            errors.append('Select an enrolled student of your institution.')
        if offering is None:
            errors.append('Select an active course offering.')
        elif student is not None and Enrollment.objects.filter(
            institution=institution, student=student, course_offering=offering
        ).exists():
            errors.append(
                f'{student.get_full_name() or student.email} is already '
                f'enrolled in {offering.course.code}.'
            )

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            with transaction.atomic():
                Enrollment.objects.create(
                    institution=institution,
                    student=student,
                    course_offering=offering,
                    status=Enrollment.EnrollmentStatus.ACTIVE,
                )
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.ENROLLMENT_CREATED,
                    description=(
                        f'Enrolled {student.get_full_name() or student.email} '
                        f'in {offering.course.code} '
                        f'({offering.semester.name}).'
                    ),
                )
            messages.success(request, 'Student enrolled.')
            return redirect('academics:enrollment_list')

    return render(request, 'academics/enrollment_form.html', {
        'institution': institution,
        'students': students,
        'offerings': offerings,
    })


@login_required
@role_required([Role.REGISTRAR, Role.INSTITUTION_ADMIN])
def final_result_view(request, enrollment_id):
    """Record or update the official final result for one enrolment."""
    institution = get_active_institution(request)
    enrollment = get_object_or_404(
        Enrollment.objects.select_related(
            'student', 'course_offering__course', 'course_offering__semester'
        ),
        id=enrollment_id,
        institution=institution,
    )

    previous = {
        'score': enrollment.final_score,
        'grade': enrollment.final_grade,
        'point': enrollment.grade_point,
    }

    if request.method == 'POST':
        score_raw = request.POST.get('final_score', '').strip()
        grade = request.POST.get('final_grade', '').strip()[:5]
        point_raw = request.POST.get('grade_point', '').strip()
        clear = request.POST.get('clear') == '1'

        errors = []
        final_score = None
        grade_point = None

        if clear:
            enrollment.final_score = None
            enrollment.final_grade = ''
            enrollment.grade_point = None
            enrollment.save(update_fields=[
                'final_score', 'final_grade', 'grade_point', 'updated_at'
            ])
            record_audit(
                request=request,
                institution=institution,
                action=AuditLog.ActionChoices.FINAL_RESULT_RECORDED,
                description=(
                    f'Cleared the recorded final result of '
                    f'{enrollment.student.get_full_name() or enrollment.student.email} '
                    f'in {enrollment.course_offering.course.code}.'
                ),
            )
            messages.success(request, 'Recorded result cleared.')
            return redirect('academics:registrar_results')

        if score_raw:
            try:
                final_score = float(score_raw)
                if final_score < 0 or final_score > 100:
                    errors.append('Final score must be between 0 and 100.')
            except ValueError:
                errors.append('Final score must be a number.')
        if point_raw:
            try:
                grade_point = float(point_raw)
                if grade_point < 0 or grade_point > 5:
                    errors.append('Grade point must be between 0.0 and 5.0.')
            except ValueError:
                errors.append('Grade point must be a number.')
        if grade and not final_score:
            errors.append('Enter a final score alongside the grade.')

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            with transaction.atomic():
                enrollment.final_score = final_score
                enrollment.final_grade = grade
                enrollment.grade_point = grade_point
                enrollment.save(update_fields=[
                    'final_score', 'final_grade', 'grade_point', 'updated_at'
                ])
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.FINAL_RESULT_RECORDED,
                    description=(
                        f'Recorded final result for '
                        f'{enrollment.student.get_full_name() or enrollment.student.email} '
                        f'in {enrollment.course_offering.course.code}: '
                        f'score {previous["score"]} -> {final_score}, '
                        f'grade "{previous["grade"]}" -> "{grade}", '
                        f'point {previous["point"]} -> {grade_point}.'
                    ),
                )
            messages.success(request, 'Final result recorded.')
            return redirect('academics:registrar_results')

    return render(request, 'academics/final_result_form.html', {
        'institution': institution,
        'enrollment': enrollment,
        'final_score': previous['score'],
        'final_grade': previous['grade'],
        'grade_point': previous['point'],
    })
