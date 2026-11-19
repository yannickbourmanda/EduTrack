"""
Predictions views - EduTrack

Machine-learning estimates are advisory only. Advisers see predictions for
their assigned students; institution administrators see their own institution.
Students see only their own prediction and history.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import get_active_institution, role_required
from accounts.models import Membership, MembershipStatus, Role, User
from accounts.permissions import adviser_student_ids, user_is_platform_admin
from academics.models import Semester
from dashboard.models import AuditLog

from .models import RiskLevel, RiskPrediction
from .services import RiskPredictionService


@login_required
@role_required([Role.STUDENT])
def my_risk_view(request):
    """A student's own advisory prediction and prediction history."""
    institution = get_active_institution(request)
    current_semester = Semester.objects.filter(
        institution=institution, is_current=True
    ).first()

    # Read-only: the student's current estimate is shown if one already
    # exists. Predictions are generated on demand by an authorised adviser
    # or institution administrator (POST); a plain GET never writes.
    prediction = (
        RiskPrediction.objects.filter(
            student=request.user,
            institution=institution,
            is_current=True,
        )
        .order_by('-predicted_at')
        .first()
    )

    history = RiskPrediction.objects.filter(
        student=request.user,
        institution=institution,
    ).order_by('-predicted_at')

    page_obj = Paginator(history, 15).get_page(request.GET.get('page'))

    return render(request, 'predictions/my_risk.html', {
        'prediction': prediction,
        'history': page_obj,
        'page_obj': page_obj,
        'current_semester': current_semester,
        'institution': institution,
    })


@login_required
@role_required([Role.ADVISER, Role.INSTITUTION_ADMIN])
def risk_overview_view(request):
    """
    Advisory risk overview.

    Advisers are restricted to students formally assigned to them. Institution
    administrators can view aggregated predictions for their own institution.
    """
    institution = get_active_institution(request)
    membership = request.user.memberships.filter(
        institution=institution, status=MembershipStatus.APPROVED
    ).first()
    is_adviser = membership.role == Role.ADVISER if membership else False

    predictions = RiskPrediction.objects.filter(
        institution=institution,
        is_current=True,
    ).select_related('student', 'semester')

    if is_adviser:
        allowed_ids = adviser_student_ids(request.user, institution)
        predictions = predictions.filter(student_id__in=allowed_ids)

    risk_filter = request.GET.get('level', '').strip()
    counts_queryset = predictions
    counts = {
        level: counts_queryset.filter(risk_level=level).count()
        for level in (RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW)
    }

    if risk_filter in dict(RiskLevel.choices):
        predictions = predictions.filter(risk_level=risk_filter)
    else:
        risk_filter = ''

    search = request.GET.get('q', '').strip()
    if search:
        predictions = predictions.filter(
            Q(student__first_name__icontains=search)
            | Q(student__last_name__icontains=search)
            | Q(student__matriculation_number__icontains=search)
        )

    predictions = predictions.order_by('-risk_score', 'student__last_name')
    page_obj = Paginator(predictions, 20).get_page(request.GET.get('page'))

    return render(request, 'predictions/risk_overview.html', {
        'predictions': page_obj,
        'page_obj': page_obj,
        'risk_filter': risk_filter,
        'search': search,
        'high_risk_count': counts[RiskLevel.HIGH],
        'medium_risk_count': counts[RiskLevel.MEDIUM],
        'low_risk_count': counts[RiskLevel.LOW],
        'total_count': counts[RiskLevel.HIGH] + counts[RiskLevel.MEDIUM] + counts[RiskLevel.LOW],
        'is_adviser': is_adviser,
        'institution': institution,
    })


@login_required
def model_metrics_view(request):
    """Technical model documentation, restricted to platform administrators."""
    if not user_is_platform_admin(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied('Platform administrator privileges are required.')

    metrics = RiskPredictionService.get_metrics()
    models_evaluation = metrics.get('models_evaluation', {})
    feature_importances = metrics.get('feature_importances', {})

    return render(request, 'predictions/model_metrics.html', {
        'metrics': metrics,
        'models_evaluation': models_evaluation,
        'feature_importances': feature_importances,
    })


@login_required
@role_required([Role.ADVISER, Role.INSTITUTION_ADMIN])
@require_POST
def trigger_prediction_view(request, student_id):
    """Re-run inference for a student the user is authorised to monitor."""
    institution = get_active_institution(request)
    membership = request.user.memberships.filter(
        institution=institution, status=MembershipStatus.APPROVED
    ).first()

    if membership.role == Role.ADVISER:
        allowed_ids = adviser_student_ids(request.user, institution)
        student = get_object_or_404(User, id=student_id, id__in=allowed_ids)
    else:
        student = get_object_or_404(
            User,
            id=student_id,
            memberships__institution=institution,
            memberships__role=Role.STUDENT,
            memberships__status=MembershipStatus.APPROVED,
        )

    current_semester = Semester.objects.filter(
        institution=institution, is_current=True
    ).first()
    if not current_semester:
        messages.error(request, 'No current semester is configured.')
        return redirect('predictions:risk_overview')

    prediction = RiskPredictionService.evaluate_and_predict(
        student, current_semester, institution
    )
    if prediction:
        record_audit(
            request=request,
            institution=institution,
            action=AuditLog.ActionChoices.PREDICTION_GENERATED,
            description=(
                f'Refreshed advisory risk estimate for '
                f'{student.get_full_name() or student.email}.'
            ),
        )
        messages.success(
            request,
            f'Risk estimate refreshed for '
            f'{student.get_full_name() or student.email}.'
        )
    else:
        messages.warning(
            request,
            f'No advisory estimate could be produced for '
            f'{student.get_full_name() or student.email}: there is no active '
            f'course enrolment or no recorded assessment evidence yet for the '
            f'current semester.'
        )

    return redirect('predictions:risk_overview')
