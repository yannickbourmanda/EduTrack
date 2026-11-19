"""
Monitoring views - EduTrack

Assessment creation, score entry and attendance recording.

Authorization:
- Lecturers can only open courses assigned to them.
- Institution administrators can review records inside their own institution.
- Students can only open their own results and attendance.
"""

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import get_active_institution, role_required
from accounts.models import Role
from accounts.permissions import ensure_offering_access, instructor_offerings
from academics.models import CourseOffering, Enrollment
from dashboard.models import AuditLog, record_audit

from .models import (
    Assessment,
    AssessmentResult,
    AssessmentType,
    AttendanceRecord,
    AttendanceStatus,
    ClassSession,
    ParticipationRecord,
)


def _managed_offerings(user, institution):
    """Offerings a lecturer teaches, or all offerings for an institution admin."""
    role = user.get_role_in(institution)
    if role == Role.INSTITUTION_ADMIN:
        return CourseOffering.objects.filter(
            institution=institution
        )
    return instructor_offerings(user, institution)


@login_required
@role_required([Role.INSTRUCTOR, Role.INSTITUTION_ADMIN])
def assessment_management_view(request):
    institution = get_active_institution(request)
    assessments = (
        Assessment.objects.filter(
            institution=institution,
            course_offering__in=_managed_offerings(request.user, institution),
        )
        .select_related('course_offering__course', 'course_offering__semester')
        .order_by('-id')
    )
    return render(request, 'monitoring/assessment_management.html', {
        'assessments': assessments,
        'institution': institution,
    })


@login_required
@role_required([Role.INSTRUCTOR, Role.INSTITUTION_ADMIN])
def create_assessment_view(request):
    institution = get_active_institution(request)
    offerings = (
        _managed_offerings(request.user, institution)
        .filter(is_active=True)
        .select_related('course', 'semester')
        .order_by('course__code')
    )

    if request.method == 'POST':
        offering_id = request.POST.get('course_offering', '').strip()
        title = request.POST.get('title', '').strip()
        assessment_type = request.POST.get('assessment_type', AssessmentType.ASSIGNMENT)
        max_score_raw = request.POST.get('max_score', '100').strip()
        weight_raw = request.POST.get('weight_percentage', '10').strip()
        due_date_raw = request.POST.get('due_date', '').strip()

        offering = offerings.filter(id=offering_id).first() if offering_id.isdigit() else None
        if offering is None:
            messages.error(request, 'Select a course you are assigned to teach.')
            return render(request, 'monitoring/assessment_form.html', {
                'offerings': offerings,
                'assessment_types': AssessmentType.choices,
            })

        errors = []
        if not title:
            errors.append('Assessment title is required.')

        try:
            max_score = float(max_score_raw)
            if max_score <= 0:
                errors.append('Maximum score must be greater than zero.')
        except ValueError:
            max_score = None
            errors.append('Maximum score must be a number.')

        try:
            weight = float(weight_raw)
            if weight < 0 or weight > 100:
                errors.append('Weight must be between 0 and 100.')
        except ValueError:
            weight = None
            errors.append('Weight must be a number.')

        due_date = None
        if due_date_raw:
            try:
                due_date = date.fromisoformat(due_date_raw)
            except ValueError:
                errors.append('Due date is not a valid date.')

        if assessment_type not in dict(AssessmentType.choices):
            errors.append('Select a valid assessment type.')

        if errors:
            for error in errors:
                messages.error(request, error)
        elif Assessment.objects.filter(
            institution=institution,
            course_offering=offering,
            title__iexact=title,
        ).exists():
            messages.error(
                request,
                f"An assessment titled '{title}' already exists for this course.",
            )
        else:
            with transaction.atomic():
                assessment = Assessment.objects.create(
                    institution=institution,
                    course_offering=offering,
                    title=title,
                    assessment_type=assessment_type,
                    max_score=max_score,
                    weight_percentage=weight,
                    due_date=due_date,
                )
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.ASSESSMENT_CREATED,
                    description=(
                        f"Created assessment '{title}' in "
                        f'{offering.course.code}.'
                    ),
                )
            messages.success(request, f"Assessment '{title}' created.")
            return redirect('monitoring:grade_assessment', assessment_id=assessment.id)

    return render(request, 'monitoring/assessment_form.html', {
        'offerings': offerings,
        'assessment_types': AssessmentType.choices,
    })


@login_required
@role_required([Role.INSTRUCTOR, Role.INSTITUTION_ADMIN])
def grade_assessment_view(request, assessment_id):
    institution = get_active_institution(request)
    assessment = get_object_or_404(
        Assessment.objects.select_related('course_offering__course'),
        id=assessment_id,
        institution=institution,
    )
    ensure_offering_access(request.user, assessment.course_offering, institution)

    enrollments = (
        Enrollment.objects.filter(
            course_offering=assessment.course_offering,
            institution=institution,
            status=Enrollment.EnrollmentStatus.ACTIVE,
        )
        .select_related('student')
        .order_by('student__last_name', 'student__first_name')
    )

    existing_results = {
        result.student_id: result
        for result in assessment.results.select_related('student')
    }

    if request.method == 'POST':
        errors = []
        pending_saves = []

        for enrollment in enrollments:
            score_raw = request.POST.get(f'score_{enrollment.student.id}', '').strip()
            feedback = request.POST.get(
                f'feedback_{enrollment.student.id}', ''
            ).strip()[:2000]

            if score_raw == '':
                continue

            try:
                score = float(score_raw)
            except ValueError:
                errors.append(
                    f'Invalid score for {enrollment.student.get_full_name()}.'
                )
                continue

            if score < 0 or score > float(assessment.max_score):
                errors.append(
                    f'Score for {enrollment.student.get_full_name()} must be '
                    f'between 0 and {assessment.max_score}.'
                )
                continue

            pending_saves.append((enrollment.student, score, feedback))

        if errors:
            for error in errors:
                messages.error(request, error)
        elif not pending_saves:
            messages.warning(request, 'No scores were entered.')
        else:
            with transaction.atomic():
                for student, score, feedback in pending_saves:
                    AssessmentResult.objects.update_or_create(
                        institution=institution,
                        assessment=assessment,
                        student=student,
                        defaults={
                            'score': score,
                            'feedback': feedback,
                            'graded_by': request.user,
                        },
                    )
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.GRADE_ENTRY,
                    description=(
                        f'Saved {len(pending_saves)} score(s) for '
                        f"'{assessment.title}' in "
                        f'{assessment.course_offering.course.code}.'
                    ),
                )
            messages.success(
                request,
                f'Saved scores for {len(pending_saves)} student(s).'
            )
            return redirect('monitoring:grade_assessment', assessment_id=assessment.id)

    students_rows = []
    for enrollment in enrollments:
        result = existing_results.get(enrollment.student.id)
        students_rows.append({
            'student': enrollment.student,
            'score': result.score if result else '',
            'feedback': result.feedback if result else '',
        })

    return render(request, 'monitoring/assessment_grade.html', {
        'assessment': assessment,
        'students_rows': students_rows,
        'institution': institution,
    })


@login_required
@role_required([Role.INSTRUCTOR, Role.INSTITUTION_ADMIN])
def enter_scores_view(request, offering_id):
    institution = get_active_institution(request)
    offering = get_object_or_404(
        CourseOffering.objects.select_related('course', 'semester'),
        id=offering_id,
        institution=institution,
    )
    ensure_offering_access(request.user, offering, institution)

    assessments = offering.assessments.all()
    return render(request, 'monitoring/offering_assessments.html', {
        'offering': offering,
        'assessments': assessments,
    })


@login_required
@role_required([Role.INSTRUCTOR, Role.INSTITUTION_ADMIN])
def attendance_overview_view(request):
    institution = get_active_institution(request)
    offerings = (
        _managed_offerings(request.user, institution)
        .filter(is_active=True)
        .select_related('course', 'semester')
        .order_by('course__code')
    )
    return render(request, 'monitoring/attendance_overview.html', {
        'offerings': offerings,
        'institution': institution,
    })


@login_required
@role_required([Role.INSTRUCTOR, Role.INSTITUTION_ADMIN])
def record_attendance_view(request, offering_id):
    institution = get_active_institution(request)
    offering = get_object_or_404(
        CourseOffering.objects.select_related('course', 'semester'),
        id=offering_id,
        institution=institution,
    )
    ensure_offering_access(request.user, offering, institution)

    enrollments = (
        Enrollment.objects.filter(
            course_offering=offering,
            institution=institution,
            status=Enrollment.EnrollmentStatus.ACTIVE,
        )
        .select_related('student')
        .order_by('student__last_name', 'student__first_name')
    )

    today = timezone.localdate()
    valid_statuses = dict(AttendanceStatus.choices)

    if request.method == 'POST':
        session_date_raw = request.POST.get('session_date', today.isoformat())
        topic = request.POST.get('topic', '').strip()[:255]

        try:
            session_date = date.fromisoformat(session_date_raw)
        except ValueError:
            messages.error(request, 'Enter a valid session date.')
            return redirect('monitoring:record_attendance', offering_id=offering.id)

        errors = []
        attendance_values = []
        participation_values = []

        for enrollment in enrollments:
            status_value = request.POST.get(
                f'status_{enrollment.student.id}', AttendanceStatus.PRESENT
            )
            participation_raw = request.POST.get(
                f'part_{enrollment.student.id}', '3'
            )
            note = request.POST.get(
                f'notes_{enrollment.student.id}', ''
            ).strip()[:255]

            if status_value not in valid_statuses:
                errors.append(
                    f'Invalid attendance status for '
                    f'{enrollment.student.get_full_name()}.'
                )
                continue

            try:
                participation = int(participation_raw)
                if participation < 1 or participation > 5:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(
                    f'Participation for {enrollment.student.get_full_name()} '
                    'must be between 1 and 5.'
                )
                participation = 3

            attendance_values.append((enrollment.student, status_value, note))
            participation_values.append((enrollment.student, participation))

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            with transaction.atomic():
                class_session, _ = ClassSession.objects.get_or_create(
                    institution=institution,
                    course_offering=offering,
                    session_date=session_date,
                    defaults={'topic': topic},
                )
                if topic and class_session.topic != topic:
                    class_session.topic = topic
                    class_session.save(update_fields=['topic', 'updated_at'])

                for student, status_value, note in attendance_values:
                    AttendanceRecord.objects.update_or_create(
                        institution=institution,
                        class_session=class_session,
                        student=student,
                        defaults={'status': status_value, 'notes': note},
                    )

                for student, participation in participation_values:
                    ParticipationRecord.objects.update_or_create(
                        institution=institution,
                        class_session=class_session,
                        student=student,
                        defaults={'score': participation},
                    )

                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.ATTENDANCE_RECORDED,
                    description=(
                        f'Recorded attendance for {offering.course.code} '
                        f'on {session_date.isoformat()}.'
                    ),
                )

            messages.success(
                request,
                f'Attendance recorded for {session_date.strftime("%d %b %Y")}.'
            )
            return redirect('monitoring:record_attendance', offering_id=offering.id)

    past_sessions = offering.class_sessions.order_by('-session_date')[:10]

    return render(request, 'monitoring/attendance_record.html', {
        'offering': offering,
        'enrollments': enrollments,
        'today': today,
        'past_sessions': past_sessions,
        'attendance_statuses': AttendanceStatus.choices,
    })


@login_required
@role_required([Role.STUDENT])
def my_grades_view(request):
    institution = get_active_institution(request)
    results = (
        AssessmentResult.objects.filter(
            student=request.user,
            institution=institution,
        )
        .select_related('assessment__course_offering__course')
        .order_by('-graded_at')
    )
    page_obj = Paginator(results, 30).get_page(request.GET.get('page'))
    return render(request, 'monitoring/my_grades.html', {
        'results': page_obj,
        'page_obj': page_obj,
        'institution': institution,
    })


@login_required
@role_required([Role.STUDENT])
def my_attendance_view(request):
    institution = get_active_institution(request)
    records = (
        AttendanceRecord.objects.filter(
            student=request.user,
            institution=institution,
        )
        .select_related('class_session__course_offering__course')
        .order_by('-class_session__session_date')
    )
    page_obj = Paginator(records, 40).get_page(request.GET.get('page'))
    return render(request, 'monitoring/my_attendance.html', {
        'records': page_obj,
        'page_obj': page_obj,
        'institution': institution,
    })
