"""
Dashboard App - EduTrack
Audit logging for institutional security, accountability, and role-based portal management.
"""

from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from institutions.models import Institution


class AuditLog(models.Model):
    """
    Security and accountability audit trail for academic and administrative
    actions. Tracks the actor, action type, affected record, timestamp,
    origin (IP address and user agent) and — where known — the institution.

    Append-only by design: the application never updates or deletes rows
    here, and nothing registers the model with the Django admin.

    ``institution`` is nullable so that platform-wide events (failed sign-in
    attempts, platform administrator actions without an institutional
    context) can still be recorded.
    """
    class ActionChoices(models.TextChoices):
        LOGIN = 'LOGIN', _('User Login')
        LOGIN_FAILED = 'LOGIN_FAILED', _('Failed Sign-in Attempt')
        LOGOUT = 'LOGOUT', _('User Logout')
        REGISTRATION_REQUESTED = 'REGISTRATION_REQUESTED', _('Membership Requested')
        ROLE_APPROVAL = 'ROLE_APPROVAL', _('Role Approval')
        ROLE_REJECTION = 'ROLE_REJECTION', _('Role Rejection')
        MEMBERSHIP_SUSPENDED = 'MEMBERSHIP_SUSPENDED', _('Membership Suspended')
        MEMBERSHIP_REACTIVATED = 'MEMBERSHIP_REACTIVATED', _('Membership Reactivated')
        ROLE_CHANGED = 'ROLE_CHANGED', _('Role Changed')
        ACCESS_DENIED = 'ACCESS_DENIED', _('Access Denied')
        PASSWORD_CHANGED = 'PASSWORD_CHANGED', _('Password Changed')
        PASSWORD_RESET_REQUESTED = 'PASSWORD_RESET_REQUESTED', _('Password Reset Requested')
        INSTITUTION_REGISTERED = 'INSTITUTION_REGISTERED', _('Institution Registered')
        INSTITUTION_SUSPENDED = 'INSTITUTION_SUSPENDED', _('Institution Suspended')
        INSTITUTION_ACTIVATED = 'INSTITUTION_ACTIVATED', _('Institution Activated')
        INSTITUTION_ADMIN_APPOINTED = 'INSTITUTION_ADMIN_APPOINTED', _('First Administrator Appointed')
        INSTITUTION_BRANDING_UPDATED = 'INSTITUTION_BRANDING_UPDATED', _('Institution Branding Updated')
        SESSION_CREATED = 'SESSION_CREATED', _('Academic Session Created')
        SEMESTER_CREATED = 'SEMESTER_CREATED', _('Semester Created')
        COURSE_CREATED = 'COURSE_CREATED', _('Course Added to Catalogue')
        ASSESSMENT_CREATED = 'ASSESSMENT_CREATED', _('Assessment Created')
        STRUCTURE_CREATED = 'STRUCTURE_CREATED', _('Faculty/Department/Programme Created')
        OFFERING_CREATED = 'OFFERING_CREATED', _('Course Offering Created')
        ENROLLMENT_CREATED = 'ENROLLMENT_CREATED', _('Student Enrolled')
        FINAL_RESULT_RECORDED = 'FINAL_RESULT_RECORDED', _('Final Result Recorded')
        GRADE_ENTRY = 'GRADE_ENTRY', _('Grade Entry / Update')
        ATTENDANCE_RECORDED = 'ATTENDANCE_RECORDED', _('Attendance Submission')
        INTERVENTION_CREATED = 'INTERVENTION_CREATED', _('Intervention Initiated')
        ADVISER_ASSIGNED = 'ADVISER_ASSIGNED', _('Adviser Assigned to Student')
        PREDICTION_GENERATED = 'PREDICTION_GENERATED', _('Risk Prediction Generated')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_actions'
    )
    institution = models.ForeignKey(
        Institution,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    action = models.CharField(max_length=40, choices=ActionChoices.choices)
    description = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)
    # Kept from the previous tenant-aware base class so the migration is
    # purely additive; ``timestamp`` remains the authoritative event time.
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Audit Log Entry')
        verbose_name_plural = _('Audit Log Entries')
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['institution', 'action']),
            models.Index(fields=['timestamp']),
        ]

    def __str__(self):
        actor = self.user.get_full_name() if self.user else "System"
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] {actor} - {self.get_action_display()}"


def client_ip_from_request(request):
    """
    Best-effort client IP for audit rows. Honours the first entry of
    ``X-Forwarded-For`` when the deployment sets ``DJANGO_BEHIND_PROXY``,
    otherwise uses the socket address. Returns ``None`` when unavailable.
    """
    if request is None:
        return None
    from django.conf import settings as django_settings
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if getattr(django_settings, 'SECURE_PROXY_SSL_HEADER', None) and forwarded:
        candidate = forwarded.split(',')[0].strip()
        if candidate:
            return candidate
    return request.META.get('REMOTE_ADDR') or None


def record_audit(*, action, description, user=None, institution=None, request=None):
    """
    Append one audit entry, deriving the origin details from the request when
    one is available. Intentionally ignores any ``created_at``/``updated_at``
    bookkeeping: rows are written once and never modified.
    """
    ip_address = None
    user_agent = ''
    if request is not None:
        ip_address = client_ip_from_request(request)
        user_agent = (request.META.get('HTTP_USER_AGENT') or '')[:255]
    if user is None and request is not None:
        user = getattr(request, 'user', None) or None
        if user is not None and not user.is_authenticated:
            user = None
    return AuditLog.objects.create(
        user=user,
        institution=institution,
        action=action,
        description=description,
        ip_address=ip_address,
        user_agent=user_agent,
    )
