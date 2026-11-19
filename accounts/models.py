"""
Accounts App - EduTrack
Contains the Custom User Model and Multi-Institution Membership Model.

OOSC Principles Applied:
- Inheritance: User inherits from Django's AbstractUser.
- Encapsulation: User and Membership models encapsulate role validation,
  permission checks, and institutional boundary logic in class methods.
"""

from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    """
    Principal system roles as defined in EduTrack specifications.
    Roles are tied to specific Institutions via the Membership entity.
    """
    PLATFORM_ADMIN = 'PLATFORM_ADMIN', _('Platform Administrator')
    INSTITUTION_ADMIN = 'INSTITUTION_ADMIN', _('Institution Administrator')
    REGISTRAR = 'REGISTRAR', _('Registrar')
    INSTRUCTOR = 'INSTRUCTOR', _('Instructor / Lecturer')
    ADVISER = 'ADVISER', _('Academic Adviser')
    STUDENT = 'STUDENT', _('Student')


class MembershipStatus(models.TextChoices):
    """
    Status of an institutional membership request.
    Enforces administrative approval workflows.
    """
    PENDING = 'PENDING', _('Pending Approval')
    APPROVED = 'APPROVED', _('Approved / Active')
    REJECTED = 'REJECTED', _('Rejected')
    SUSPENDED = 'SUSPENDED', _('Suspended')


class User(AbstractUser):
    """
    Central Custom User Model for EduTrack.
    Uses email as a unique identifier alongside username.
    """
    email = models.EmailField(_('email address'), unique=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    matriculation_number = models.CharField(max_length=50, blank=True, null=True, help_text=_("Student ID / Matric Number if applicable"))
    staff_id = models.CharField(max_length=50, blank=True, null=True, help_text=_("Staff / Employee ID if applicable"))
    profile_bio = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Note: is_email_verified supports registration verification workflows
    is_email_verified = models.BooleanField(default=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'first_name', 'last_name']

    class Meta:
        verbose_name = _('User')
        verbose_name_plural = _('Users')
        ordering = ['last_name', 'first_name']

    def __str__(self):
        full = self.get_full_name()
        return f"{full} ({self.email})" if full else self.email

    def get_role_in(self, institution):
        """Returns the approved role for this user in the specified institution, or None."""
        membership = self.memberships.filter(institution=institution, status=MembershipStatus.APPROVED).first()
        return membership.role if membership else None

    def is_platform_admin(self):
        """Checks if the user has platform administrator capabilities."""
        if self.is_superuser:
            return True
        return self.memberships.filter(role=Role.PLATFORM_ADMIN, status=MembershipStatus.APPROVED).exists()

    def is_student_in(self, institution):
        return self.get_role_in(institution) == Role.STUDENT

    def is_instructor_in(self, institution):
        return self.get_role_in(institution) == Role.INSTRUCTOR

    def is_adviser_in(self, institution):
        return self.get_role_in(institution) == Role.ADVISER

    def is_registrar_in(self, institution):
        return self.get_role_in(institution) == Role.REGISTRAR

    def is_admin_in(self, institution):
        return self.get_role_in(institution) in [Role.INSTITUTION_ADMIN, Role.PLATFORM_ADMIN] or self.is_superuser


class Membership(models.Model):
    """
    Multi-Tenancy Bridge Entity.
    Connects a User to an Institution with a specific Role and Approval Status.
    
    A user can be an Instructor at University A and an Academic Adviser at University B,
    or a Student at University A. Roles are strictly tenant-isolated.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='memberships')
    institution = models.ForeignKey('institutions.Institution', on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=30, choices=Role.choices, default=Role.STUDENT)
    status = models.CharField(max_length=20, choices=MembershipStatus.choices, default=MembershipStatus.PENDING)
    
    # Department affiliation within the institution (e.g. Department of Computer Science)
    department = models.ForeignKey('institutions.Department', on_delete=models.SET_NULL, null=True, blank=True, related_name='department_members')
    
    # Academic program for students
    programme = models.ForeignKey('institutions.Programme', on_delete=models.SET_NULL, null=True, blank=True, related_name='enrolled_students')

    # Approval and audit metadata
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_memberships')
    rejection_reason = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = _('Institutional Membership')
        verbose_name_plural = _('Institutional Memberships')
        unique_together = ('user', 'institution')
        indexes = [
            models.Index(fields=['user', 'institution', 'status']),
            models.Index(fields=['institution', 'role', 'status']),
        ]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.email} - {self.institution.code} ({self.get_role_display()} - {self.get_status_display()})"

    def clean(self):
        super().clean()
        # An optional department/programme affiliation must belong to the
        # same institution as the membership itself.
        for field_name in ('department', 'programme'):
            related = getattr(self, field_name, None)
            if related is not None and related.institution_id != self.institution_id:
                raise ValidationError(
                    {
                        field_name: _(
                            '%(object)s belongs to a different institution.'
                        )
                        % {'object': related}
                    }
                )

    def approve(self, admin_user):
        """Encapsulated business logic to approve a membership."""
        from django.utils import timezone
        self.status = MembershipStatus.APPROVED
        self.reviewed_by = admin_user
        self.reviewed_at = timezone.now()
        self.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    def reject(self, admin_user, reason=''):
        """Encapsulated business logic to reject a membership."""
        from django.utils import timezone
        self.status = MembershipStatus.REJECTED
        self.reviewed_by = admin_user
        self.reviewed_at = timezone.now()
        self.rejection_reason = reason
        self.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'rejection_reason'])
