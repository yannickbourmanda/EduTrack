"""
Academics App - EduTrack
Academic sessions, semesters, courses, course offerings, and student enrollment.

OOSC Principles Applied:
- Encapsulation: CourseOffering encapsulates methods to compute class averages,
  enrolled count, and attendance rates.
- Inheritance: Subclasses TenantAwareModel to inherit multi-tenant isolation.
"""

from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from institutions.models import TenantAwareModel, TenantConsistencyMixin, Department


class AcademicSession(TenantAwareModel):
    """
    Academic Year / Session (e.g. 2025/2026 Academic Year).
    """
    name = models.CharField(max_length=50, help_text=_("e.g. 2025/2026"))
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)

    class Meta:
        verbose_name = _('Academic Session')
        verbose_name_plural = _('Academic Sessions')
        unique_together = ('institution', 'name')
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.name} - {self.institution.code}"

    def save(self, *args, **kwargs):
        if self.is_current:
            # Ensure only one session is marked current per institution
            AcademicSession.objects.filter(institution=self.institution, is_current=True).exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)


class Semester(TenantConsistencyMixin, TenantAwareModel):
    """
    Semester within an Academic Session (e.g., First / Fall Semester).
    """
    TENANT_FK_FIELDS = ('session',)

    class TermChoices(models.TextChoices):
        FIRST = 'FIRST', _('First / Fall Semester')
        SECOND = 'SECOND', _('Second / Spring Semester')
        SUMMER = 'SUMMER', _('Summer Session')

    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name='semesters')
    name = models.CharField(max_length=100)
    term = models.CharField(max_length=20, choices=TermChoices.choices, default=TermChoices.FIRST)
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)

    class Meta:
        verbose_name = _('Semester')
        verbose_name_plural = _('Semesters')
        unique_together = ('institution', 'session', 'term')
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.name} ({self.session.name})"

    def save(self, *args, **kwargs):
        if self.is_current:
            # Ensure only one semester is marked current per institution
            Semester.objects.filter(institution=self.institution, is_current=True).exclude(pk=self.pk).update(is_current=False)
        self.clean()
        super().save(*args, **kwargs)


class Course(TenantConsistencyMixin, TenantAwareModel):
    """
    Course Catalogue Definition (e.g. CSC 301 - Object-Oriented Software Construction).
    """
    TENANT_FK_FIELDS = ('department',)

    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='courses')
    code = models.CharField(max_length=20, help_text=_("e.g. CSC301"))
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    credit_units = models.PositiveSmallIntegerField(default=3)

    class Meta:
        verbose_name = _('Course')
        verbose_name_plural = _('Courses')
        unique_together = ('institution', 'code')
        ordering = ['code']

    def __str__(self):
        return f"{self.code}: {self.title} ({self.credit_units} credits)"


class CourseOffering(TenantConsistencyMixin, TenantAwareModel):
    """
    A specific instance of a course taught during a semester by an instructor.
    Connects: Course + AcademicSession + Semester + Instructor + Enrolled Students.
    """
    TENANT_FK_FIELDS = ('course', 'session', 'semester')

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='offerings')
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name='course_offerings')
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='course_offerings')
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='instructed_offerings'
    )
    syllabus = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('Course Offering')
        verbose_name_plural = _('Course Offerings')
        unique_together = ('institution', 'course', 'session', 'semester')
        ordering = ['course__code']

    def __str__(self):
        instructor_name = self.instructor.get_full_name() if self.instructor else "TBA"
        return f"{self.course.code} ({self.semester.name}) - {instructor_name}"

    def enrolled_students_count(self):
        return self.enrollments.filter(status='ACTIVE').count()

    def get_average_score(self):
        """Calculates current course aggregate score from final grades or assessments."""
        scores = self.enrollments.filter(status='ACTIVE', final_score__isnull=False).values_list('final_score', flat=True)
        if not scores:
            return 0.0
        return round(sum(scores) / len(scores), 2)


class Enrollment(TenantConsistencyMixin, TenantAwareModel):
    """
    Student Enrollment in a Course Offering.
    Tracks academic status and computed final grade.
    """
    TENANT_FK_FIELDS = ('course_offering',)

    class EnrollmentStatus(models.TextChoices):
        ACTIVE = 'ACTIVE', _('Active')
        DROPPED = 'DROPPED', _('Dropped')
        COMPLETED = 'COMPLETED', _('Completed')

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='course_enrollments'
    )
    course_offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.CASCADE,
        related_name='enrollments'
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=EnrollmentStatus.choices, default=EnrollmentStatus.ACTIVE)
    
    # Official final scores (calculated or recorded by registrar/instructor)
    final_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    final_grade = models.CharField(max_length=5, blank=True, default='')
    grade_point = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = _('Enrollment')
        verbose_name_plural = _('Enrollments')
        unique_together = ('institution', 'student', 'course_offering')
        ordering = ['course_offering__course__code']

    def __str__(self):
        return f"{self.student.get_full_name() or self.student.email} in {self.course_offering.course.code}"

    def clean(self):
        super().clean()
        # The student must hold an approved membership at the institution the
        # enrolment is recorded under, otherwise the row would link academic
        # history to a user who does not belong to that tenant.
        student = getattr(self, 'student', None)
        if (
            student is not None
            and not student.memberships.filter(
                institution_id=self.institution_id,
                status='APPROVED',
            ).exists()
        ):
            raise ValidationError(
                {
                    'student': _(
                        'The student must hold an approved membership at '
                        'this institution.'
                    )
                }
            )

    def compute_current_score(self):
        """
        Computes current weighted assessment score for this student in this course offering.
        Encapsulates academic grading calculations.
        """
        from monitoring.models import AssessmentResult
        results = AssessmentResult.objects.filter(
            assessment__course_offering=self.course_offering,
            student=self.student
        ).select_related('assessment')
        
        total_earned = 0.0
        total_possible = 0.0

        for r in results:
            if r.assessment.max_score > 0:
                total_earned += float(r.score)
                total_possible += float(r.assessment.max_score)

        if total_possible > 0:
            percentage = round((total_earned / total_possible) * 100, 2)
            return percentage
        return 0.0

    def get_attendance_percentage(self):
        """Calculates student's attendance rate for this course offering."""
        from monitoring.models import AttendanceRecord, AttendanceStatus
        total_sessions = AttendanceRecord.objects.filter(
            class_session__course_offering=self.course_offering,
            student=self.student
        ).count()

        if total_sessions == 0:
            return 100.0  # Default to 100 if no sessions held yet

        attended_count = AttendanceRecord.objects.filter(
            class_session__course_offering=self.course_offering,
            student=self.student,
            status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.EXCUSED]
        ).count()

        return round((attended_count / total_sessions) * 100, 1)
