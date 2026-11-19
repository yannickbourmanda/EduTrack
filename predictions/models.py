"""
Predictions App - EduTrack
Machine Learning Early Warning Risk Estimation Models and Prediction History.

OOSC Principles Applied:
- Separation of Concerns: Machine Learning prediction logic is decoupled from
  academic business logic.
- Advisory Ethical Constraint: Predictions are purely advisory and cannot execute
  punitive or automated academic disqualifications.
"""

from django.db import models
from django.db.models import Q
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from institutions.models import TenantAwareModel, TenantConsistencyMixin
from academics.models import Semester


class RiskLevel(models.TextChoices):
    LOW = 'LOW', _('Low Academic Risk')
    MEDIUM = 'MEDIUM', _('Moderate Academic Risk')
    HIGH = 'HIGH', _('High Academic Risk (Requires Intervention)')


class RiskPrediction(TenantConsistencyMixin, TenantAwareModel):
    """
    Historical and active machine learning prediction record for a student.
    Stores the model estimate, contributing factors, recommendations, and version metadata.
    """
    TENANT_FK_FIELDS = ('semester',)

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='risk_predictions'
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.CASCADE,
        related_name='risk_predictions'
    )
    risk_level = models.CharField(
        max_length=10,
        choices=RiskLevel.choices,
        default=RiskLevel.LOW
    )
    risk_score = models.FloatField(
        help_text=_("Estimated probability of academic difficulty (0.00 to 1.00)")
    )
    model_version = models.CharField(max_length=50, default='rf_academic_v1.0')
    model_name = models.CharField(max_length=100, default='Random Forest Classifier')
    
    # Explainability features
    contributing_factors = models.JSONField(
        default=list,
        help_text=_("Key academic variables influencing this risk estimation")
    )
    recommendations = models.JSONField(
        default=list,
        help_text=_("Evidence-based educational interventions recommended by the system")
    )

    # Feature snapshot at the time of prediction (for auditability and data science review)
    feature_snapshot = models.JSONField(
        default=dict,
        help_text=_("Feature vector inputs passed to the model")
    )

    is_current = models.BooleanField(default=True)
    predicted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Risk Prediction')
        verbose_name_plural = _('Risk Predictions')
        ordering = ['-predicted_at']
        indexes = [
            models.Index(fields=['student', 'is_current']),
            models.Index(fields=['institution', 'risk_level']),
        ]
        constraints = [
            # Exactly one current estimate per student, semester and
            # institution; the service deactivates the previous row inside
            # the same transaction before creating the replacement (L-04).
            models.UniqueConstraint(
                fields=['institution', 'student', 'semester'],
                condition=Q(is_current=True),
                name='uniq_current_prediction_per_semester',
            ),
        ]

    def __str__(self):
        return f"{self.student.get_full_name()} - {self.get_risk_level_display()} ({self.risk_score * 100:.1f}%)"

    @property
    def badge_color(self):
        if self.risk_level == RiskLevel.HIGH:
            return 'danger'
        elif self.risk_level == RiskLevel.MEDIUM:
            return 'warning'
        return 'success'

    @property
    def risk_percentage(self):
        return round(self.risk_score * 100, 1)
