"""
Advising views - EduTrack

Advisers only see students formally assigned to them, inside their own
institution. Intervention records are always owned by the signed-in adviser.
"""

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import get_active_institution, role_required
from accounts.models import Membership, MembershipStatus, Role, User
from accounts.permissions import adviser_assignments
from dashboard import analytics
from dashboard.models import AuditLog, record_audit
from predictions.models import RiskPrediction

from .models import (
    AcademicIntervention,
    AdviserAssignment,
    InterventionStatus,
    InterventionType,
    MeetingNote,
)


@login_required
@role_required([Role.ADVISER])
def assigned_students_view(request):
    institution = get_active_institution(request)
    assignments = (
        adviser_assignments(request.user, institution)
        .select_related('student')
        .order_by('student__last_name', 'student__first_name')
    )

    search = request.GET.get('q', '').strip()
    if search:
        assignments = assignments.filter(
            Q(student__first_name__icontains=search)
            | Q(student__last_name__icontains=search)
            | Q(student__email__icontains=search)
            | Q(student__matriculation_number__icontains=search)
        )

    page_obj = Paginator(assignments, 20).get_page(request.GET.get('page'))
    page_assignments = list(page_obj.object_list)
    student_ids = [assignment.student_id for assignment in page_assignments]

    performance_map = analytics.assessment_performance_map(institution, student_ids)
    student_attendance, _ = analytics.attendance_by_student(institution, student_ids)

    from academics.models import Enrollment

    enrollments = Enrollment.objects.filter(
        institution=institution,
        student_id__in=student_ids,
        status=Enrollment.EnrollmentStatus.ACTIVE,
    )
    scores_by_student = {}
    for enrollment in enrollments:
        score = analytics.enrollment_performance(enrollment, performance_map)
        if score is not None:
            scores_by_student.setdefault(enrollment.student_id, []).append(score)

    predictions = {
        prediction.student_id: prediction
        for prediction in RiskPrediction.objects.filter(
            institution=institution,
            student_id__in=student_ids,
            is_current=True,
        ).select_related('student')
    }

    rows = []
    for assignment in page_assignments:
        scores = scores_by_student.get(assignment.student_id, [])
        rows.append({
            'assignment': assignment,
            'student': assignment.student,
            'average': round(sum(scores) / len(scores), 1) if scores else None,
            'attendance_rate': student_attendance.get(assignment.student_id),
            'prediction': predictions.get(assignment.student_id),
        })

    return render(request, 'advising/assigned_students.html', {
        'institution': institution,
        'page_obj': page_obj,
        'students_data': rows,
        'search': search,
    })


@login_required
@role_required([Role.ADVISER])
def intervention_list_view(request):
    institution = get_active_institution(request)
    interventions = (
        AcademicIntervention.objects.filter(
            institution=institution,
            adviser=request.user,
        )
        .select_related('student')
        .order_by('-date_conducted')
    )

    status_filter = request.GET.get('status', '').strip()
    if status_filter in dict(InterventionStatus.choices):
        interventions = interventions.filter(status=status_filter)
    else:
        status_filter = ''

    page_obj = Paginator(interventions, 20).get_page(request.GET.get('page'))
    return render(request, 'advising/intervention_list.html', {
        'interventions': page_obj,
        'page_obj': page_obj,
        'status_filter': status_filter,
        'status_choices': InterventionStatus.choices,
        'institution': institution,
    })


@login_required
@role_required([Role.ADVISER])
def create_intervention_view(request, student_id=None):
    institution = get_active_institution(request)

    assigned_students = User.objects.filter(
        adviser_assignments__adviser=request.user,
        adviser_assignments__institution=institution,
        adviser_assignments__is_active=True,
    ).distinct().order_by('last_name', 'first_name')

    selected_student = None
    if student_id:
        selected_student = get_object_or_404(assigned_students, id=student_id)

    if request.method == 'POST':
        student_post = request.POST.get('student', '').strip()
        student = assigned_students.filter(id=student_post).first() if student_post.isdigit() else None

        if student is None:
            messages.error(request, 'Select one of your assigned students.')
            return render(request, 'advising/intervention_form.html', {
                'assigned_students': assigned_students,
                'selected_student': selected_student,
                'intervention_types': InterventionType.choices,
                'today': date.today(),
            })

        intervention_type = request.POST.get(
            'intervention_type', InterventionType.COUNSELLING
        )
        reason = request.POST.get('reason', '').strip()
        action_taken = request.POST.get('action_taken', '').strip()
        date_conducted_raw = request.POST.get(
            'date_conducted', date.today().isoformat()
        ).strip()
        follow_up_raw = request.POST.get('follow_up_date', '').strip()

        errors = []
        if intervention_type not in dict(InterventionType.choices):
            errors.append('Select a valid intervention type.')
        if not reason:
            errors.append('An academic reason or trigger is required.')
        if not action_taken:
            errors.append('Describe the action taken or agreed plan.')

        try:
            date_conducted = date.fromisoformat(date_conducted_raw)
        except ValueError:
            date_conducted = date.today()
            errors.append('Enter a valid intervention date.')

        follow_up_date = None
        if follow_up_raw:
            try:
                follow_up_date = date.fromisoformat(follow_up_raw)
            except ValueError:
                errors.append('Enter a valid follow-up date.')

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            with transaction.atomic():
                intervention = AcademicIntervention.objects.create(
                    institution=institution,
                    student=student,
                    adviser=request.user,
                    intervention_type=intervention_type,
                    status=InterventionStatus.IN_PROGRESS,
                    reason=reason,
                    action_taken=action_taken,
                    date_conducted=date_conducted,
                    follow_up_date=follow_up_date,
                )
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.INTERVENTION_CREATED,
                    description=(
                        f'Initiated {intervention.get_intervention_type_display()} '
                        f'for {student.get_full_name()}.'
                    ),
                )
            messages.success(
                request,
                f'Intervention recorded for {student.get_full_name()}.'
            )
            return redirect(
                'advising:intervention_detail', intervention_id=intervention.id
            )

    return render(request, 'advising/intervention_form.html', {
        'assigned_students': assigned_students,
        'selected_student': selected_student,
        'intervention_types': InterventionType.choices,
        'today': date.today(),
    })


@login_required
@role_required([Role.ADVISER])
def intervention_detail_view(request, intervention_id):
    institution = get_active_institution(request)
    intervention = get_object_or_404(
        AcademicIntervention.objects.select_related('student', 'adviser'),
        id=intervention_id,
        institution=institution,
        adviser=request.user,
    )

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_note':
            note_content = request.POST.get('note', '').strip()
            if note_content:
                with transaction.atomic():
                    MeetingNote.objects.create(
                        institution=institution,
                        intervention=intervention,
                        author=request.user,
                        content=note_content[:4000],
                    )
                messages.success(request, 'Follow-up note added.')
            else:
                messages.error(request, 'Enter a note before saving.')

        elif action == 'resolve':
            outcome = request.POST.get('outcome_notes', '').strip()
            improved = request.POST.get('is_improved') == '1'
            with transaction.atomic():
                intervention.mark_resolved(
                    outcome_notes=outcome, improved=improved
                )
            messages.success(request, 'Intervention marked as resolved.')

        else:
            messages.error(request, 'Unknown action.')

        return redirect(
            'advising:intervention_detail', intervention_id=intervention.id
        )

    notes = intervention.notes.select_related('author').order_by('created_at')
    prediction = RiskPrediction.objects.filter(
        student=intervention.student,
        institution=institution,
        is_current=True,
    ).first()

    return render(request, 'advising/intervention_detail.html', {
        'intervention': intervention,
        'notes': notes,
        'prediction': prediction,
        'institution': institution,
    })


@login_required
@role_required([Role.INSTITUTION_ADMIN])
def assign_adviser_view(request):
    """
    Assign an adviser of the institution to one of its students.

    Without this workflow an adviser could never be attached to an advisee,
    so the advising module would stay unusable outside the seed data.
    """
    institution = get_active_institution(request)

    advisers = User.objects.filter(
        memberships__institution=institution,
        memberships__role=Role.ADVISER,
        memberships__status=MembershipStatus.APPROVED,
        is_active=True,
    ).distinct().order_by('last_name', 'first_name')
    students = User.objects.filter(
        memberships__institution=institution,
        memberships__role=Role.STUDENT,
        memberships__status=MembershipStatus.APPROVED,
        is_active=True,
    ).distinct().order_by('last_name', 'first_name')

    if request.method == 'POST':
        adviser_id = request.POST.get('adviser', '').strip()
        student_id = request.POST.get('student', '').strip()

        adviser = advisers.filter(id=adviser_id).first() if adviser_id.isdigit() else None
        student = students.filter(id=student_id).first() if student_id.isdigit() else None

        errors = []
        if adviser is None:
            errors.append('Select an adviser with an approved membership.')
        if student is None:
            errors.append('Select a student with an approved membership.')
        elif adviser is not None and AdviserAssignment.objects.filter(
            institution=institution, adviser=adviser, student=student
        ).exists():
            errors.append('That adviser is already assigned to this student.')

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            with transaction.atomic():
                AdviserAssignment.objects.create(
                    institution=institution,
                    adviser=adviser,
                    student=student,
                    is_active=True,
                )
                record_audit(
                    request=request,
                    institution=institution,
                    action=AuditLog.ActionChoices.ADVISER_ASSIGNED,
                    description=(
                        f'Assigned {adviser.get_full_name() or adviser.email} '
                        f'as academic adviser to '
                        f'{student.get_full_name() or student.email}.'
                    ),
                )
            messages.success(request, 'Adviser assigned.')
            return redirect('advising:assign_adviser')

    assignments = (
        AdviserAssignment.objects.filter(institution=institution)
        .select_related('adviser', 'student')
        .order_by('student__last_name', 'student__first_name')
    )

    return render(request, 'advising/assign_adviser.html', {
        'institution': institution,
        'advisers': advisers,
        'students': students,
        'assignments': assignments,
    })
