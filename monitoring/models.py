"""
Monitoring App - EduTrack
Academic performance monitoring: assessments, continuous scores, attendance, and participation.

OOSC Principles Applied:
- Encapsulation: Result validation ensures scores cannot be negative or exceed max_score.
- Reusability: Institutional grading scale allows configurable university grade bounds.
"""

from django.db import models
from django.db.models import Q
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from institutions.models import TenantAwareModel, TenantConsistencyMixin
from academics.models import CourseOffering


class GradingScale(TenantAwareModel):
    """
    Configurable grading policy per institution.
    Supports varying university grading standards (e.g. 70-100 = A, 60-69 = B, etc.)
    """
    grade = models.CharField(max_length=5, help_text=_("e.g. A, B, C, D, F"))
    min_score = models.DecimalField(max_digits=5, decimal_places=2)
    max_score = models.DecimalField(max_digits=5, decimal_places=2)
    grade_point = models.DecimalField(max_digits=3, decimal_places=2, help_text=_("e.g. 5.0 for A, 4.0 for B, etc."))
    description = models.CharField(max_length=50, blank=True, default='')

    class Meta:
        verbose_name = _('Grading Scale Entry')
        verbose_name_plural = _('Grading Scale Entries')
        unique_together = ('institution', 'grade')
        ordering = ['-min_score']

    def __str__(self):
        return f"{self.grade} ({self.min_score} - {self.max_score}%) - GP: {self.grade_point}"


class AssessmentType(models.TextChoices):
    ASSIGNMENT = 'ASSIGNMENT', _('Assignment')
    QUIZ = 'QUIZ', _('Quiz')
    TEST = 'TEST', _('Test / CA')
    MIDTERM = 'MIDTERM', _('Mid-Semester Examination')
    FINAL_EXAM = 'FINAL_EXAM', _('Final Examination')
    PROJECT = 'PROJECT', _('Project / Lab')


class Assessment(TenantConsistencyMixin, TenantAwareModel):
    """
    Assessment created by an Instructor for a specific Course Offering.
    """
    TENANT_FK_FIELDS = ('course_offering',)

    course_offering = models.ForeignKey(CourseOffering, on_delete=models.CASCADE, related_name='assessments')
    title = models.CharField(max_length=255)
    assessment_type = models.CharField(max_length=20, choices=AssessmentType.choices, default=AssessmentType.ASSIGNMENT)
    max_score = models.DecimalField(max_digits=5, decimal_places=2, default=100.0)
    weight_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=10.0, help_text=_("Contribution percentage to overall grade"))
    due_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = _('Assessment')
        verbose_name_plural = _('Assessments')
        ordering = ['due_date', 'title']
        constraints = [
            # A repeated title for the same offering would be a genuine
            # duplicate assessment, not a revision (H-08).
            models.UniqueConstraint(
                fields=['institution', 'course_offering', 'title'],
                name='uniq_assessment_title_per_offering',
            ),
            models.CheckConstraint(
                condition=Q(max_score__gt=0),
                name='assessment_max_score_positive',
            ),
            models.CheckConstraint(
                condition=Q(weight_percentage__gte=0, weight_percentage__lte=100),
                name='assessment_weight_between_0_and_100',
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_assessment_type_display()}) - {self.course_offering.course.code}"

    def clean(self):
        if self.max_score <= 0:
            raise ValidationError({'max_score': _('Maximum score must be greater than zero.')})
        if self.weight_percentage < 0 or self.weight_percentage > 100:
            raise ValidationError({'weight_percentage': _('Weight percentage must be between 0 and 100.')})


class AssessmentResult(TenantConsistencyMixin, TenantAwareModel):
    """
    Individual student score for a specific assessment.
    Includes validation preventing negative or out-of-bounds scores.
    """
    TENANT_FK_FIELDS = ('assessment',)

    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name='results')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assessment_scores')
    score = models.DecimalField(max_digits=5, decimal_places=2)
    feedback = models.TextField(blank=True, default='')
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='grades_given'
    )
    graded_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Assessment Result')
        verbose_name_plural = _('Assessment Results')
        unique_together = ('institution', 'assessment', 'student')
        indexes = [
            models.Index(fields=['student', 'institution']),
            models.Index(fields=['assessment', 'student']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(score__gte=0),
                name='assessment_result_score_non_negative',
            ),
        ]

    def __str__(self):
        return f"{self.student.get_full_name()} - {self.assessment.title}: {self.score}/{self.assessment.max_score}"

    def clean(self):
        super().clean()
        if self.score < 0:
            raise ValidationError({'score': _('Score cannot be negative.')})
        if self.score > self.assessment.max_score:
            raise ValidationError({'score': _(f'Score ({self.score}) cannot exceed maximum score ({self.assessment.max_score}).')})

    @property
    def percentage(self):
        if self.assessment.max_score > 0:
            return round((float(self.score) / float(self.assessment.max_score)) * 100, 1)
        return 0.0


class ClassSession(TenantConsistencyMixin, TenantAwareModel):
    """
    A single class session / lecture period for recording attendance and participation.
    """
    TENANT_FK_FIELDS = ('course_offering',)

    course_offering = models.ForeignKey(CourseOffering, on_delete=models.CASCADE, related_name='class_sessions')
    session_date = models.DateField()
    topic = models.CharField(max_length=255, blank=True, default='')
    notes = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = _('Class Session')
        verbose_name_plural = _('Class Sessions')
        ordering = ['-session_date']
        constraints = [
            # Two sessions for the same offering on the same date would split
            # attendance and participation across rows (H-08).
            models.UniqueConstraint(
                fields=['institution', 'course_offering', 'session_date'],
                name='uniq_class_session_per_offering_day',
            ),
        ]

    def __str__(self):
        return f"{self.course_offering.course.code} - {self.session_date} ({self.topic or 'Lecture'})"


class AttendanceStatus(models.TextChoices):
    PRESENT = 'PRESENT', _('Present')
    ABSENT = 'ABSENT', _('Absent')
    LATE = 'LATE', _('Late')
    EXCUSED = 'EXCUSED', _('Excused Absence')


class AttendanceRecord(TenantConsistencyMixin, TenantAwareModel):
    """
    Attendance record for an individual student in a specific class session.
    """
    TENANT_FK_FIELDS = ('class_session',)

    class_session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name='attendance_records')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attendance_records')
    status = models.CharField(max_length=10, choices=AttendanceStatus.choices, default=AttendanceStatus.PRESENT)
    notes = models.CharField(max_length=255, blank=True, default='')
    recorded_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Attendance Record')
        verbose_name_plural = _('Attendance Records')
        unique_together = ('institution', 'class_session', 'student')

    def __str__(self):
        return f"{self.student.get_full_name()} - {self.class_session.session_date}: {self.get_status_display()}"


class ParticipationRecord(TenantConsistencyMixin, TenantAwareModel):
    """
    Class participation record using a clear, structured scale (1 to 5).
    1: Minimal, 2: Passive, 3: Active, 4: Very Engaged, 5: Outstanding / Exemplary
    """
    TENANT_FK_FIELDS = ('class_session',)

    PARTICIPATION_SCALE = [
        (1, '1 - Minimal / Disengaged'),
        (2, '2 - Passive Observer'),
        (3, '3 - Active Participant'),
        (4, '4 - Highly Engaged'),
        (5, '5 - Exemplary Contribution'),
    ]

    class_session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name='participation_records')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='participation_records')
    score = models.PositiveSmallIntegerField(choices=PARTICIPATION_SCALE, default=3)
    notes = models.TextField(blank=True, default='')
    recorded_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Participation Record')
        verbose_name_plural = _('Participation Records')
        unique_together = ('institution', 'class_session', 'student')
        constraints = [
            models.CheckConstraint(
                condition=Q(score__gte=1, score__lte=5),
                name='participation_score_between_1_and_5',
            ),
        ]

    def __str__(self):
        return f"{self.student.get_full_name()} - Participation: {self.score}/5"
