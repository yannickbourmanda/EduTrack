"""
Advising App - EduTrack
Academic advising, student-adviser assignments, intervention logging, and outcome monitoring.

OOSC Principles Applied:
- Encapsulation: AcademicIntervention encapsulates workflow state transitions
  (e.g., mark_resolved, schedule_followup).
- Multi-tenancy: Strictly isolates adviser-student assignments and records within the institution.
"""

from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from institutions.models import TenantAwareModel, TenantConsistencyMixin


class AdviserAssignment(TenantConsistencyMixin, TenantAwareModel):
    """
    Formal assignment of an Academic Adviser to a Student within an Institution.
    Advisers only monitor students explicitly assigned to them or within their department.
    """
    adviser = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='advised_students'
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='adviser_assignments'
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = _('Adviser Assignment')
        verbose_name_plural = _('Adviser Assignments')
        unique_together = ('institution', 'adviser', 'student')
        ordering = ['student__last_name', 'student__first_name']

    def __str__(self):
        return f"Adviser {self.adviser.get_full_name()} -> Student {self.student.get_full_name()}"

    def clean(self):
        super().clean()
        # Both parties must hold an approved membership at this institution,
        # otherwise an assignment could cross an institutional boundary.
        from accounts.models import MembershipStatus

        for field_name in ('adviser', 'student'):
            person = getattr(self, field_name, None)
            if person is not None and not person.memberships.filter(
                institution_id=self.institution_id,
                status=MembershipStatus.APPROVED,
            ).exists():
                raise ValidationError(
                    {
                        field_name: _(
                            'This user must hold an approved membership at '
                            'this institution.'
                        )
                    }
                )


class InterventionType(models.TextChoices):
    COUNSELLING = 'COUNSELLING', _('Academic Counselling')
    STUDY_PLAN = 'STUDY_PLAN', _('Study Planning & Time Management')
    TUTORING_REFERRAL = 'TUTORING_REFERRAL', _('Course Support / Tutoring Referral')
    ATTENDANCE_FOLLOWUP = 'ATTENDANCE_FOLLOWUP', _('Attendance Follow-up')
    GENERAL_CONSULTATION = 'GENERAL_CONSULTATION', _('General Academic Consultation')
    PROBATION_REVIEW = 'PROBATION_REVIEW', _('Academic Probation Review')


class InterventionStatus(models.TextChoices):
    OPEN = 'OPEN', _('Open / Scheduled')
    IN_PROGRESS = 'IN_PROGRESS', _('In Progress')
    RESOLVED = 'RESOLVED', _('Resolved / Completed')
    CANCELLED = 'CANCELLED', _('Cancelled')


class AcademicIntervention(TenantAwareModel):
    """
    Structured record of an academic intervention conducted by an Adviser for an assigned student.
    Tracks issues identified, action plans formulated, follow-up dates, and observed outcomes.
    """
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='interventions_received'
    )
    adviser = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='interventions_conducted'
    )
    intervention_type = models.CharField(
        max_length=30,
        choices=InterventionType.choices,
        default=InterventionType.COUNSELLING
    )
    status = models.CharField(
        max_length=20,
        choices=InterventionStatus.choices,
        default=InterventionStatus.OPEN
    )
    reason = models.TextField(help_text=_("Academic trigger or difficulty reason (e.g. Low quiz marks, 40% attendance drop)"))
    action_taken = models.TextField(help_text=_("Details of advice given, corrective milestones agreed upon"))
    date_conducted = models.DateField(default=timezone.now)
    follow_up_date = models.DateField(null=True, blank=True)
    outcome_notes = models.TextField(blank=True, default='', help_text=_("Documented student progress upon follow-up"))
    is_improved = models.BooleanField(null=True, blank=True, help_text=_("Whether academic improvement was observed"))

    class Meta:
        verbose_name = _('Academic Intervention')
        verbose_name_plural = _('Academic Interventions')
        ordering = ['-date_conducted']

    def __str__(self):
        return f"{self.get_intervention_type_display()} - {self.student.get_full_name()} ({self.get_status_display()})"

    def mark_resolved(self, outcome_notes='', improved=True):
        """Encapsulated workflow method to complete an intervention."""
        self.status = InterventionStatus.RESOLVED
        self.outcome_notes = outcome_notes
        self.is_improved = improved
        self.save(update_fields=['status', 'outcome_notes', 'is_improved', 'updated_at'])


class MeetingNote(TenantAwareModel):
    """
    Chronological progress note or follow-up comment added to an ongoing intervention.
    """
    intervention = models.ForeignKey(AcademicIntervention, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    content = models.TextField()

    class Meta:
        verbose_name = _('Meeting Note')
        verbose_name_plural = _('Meeting Notes')
        ordering = ['created_at']

    def __str__(self):
        return f"Note by {self.author.get_full_name()} on {self.created_at.strftime('%Y-%m-%d')}"
