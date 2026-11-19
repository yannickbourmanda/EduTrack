"""
Institutions App - EduTrack
Multi-institution organization structure models.

OOSC Principles Applied:
- Abstraction: TenantAwareModel serves as an abstract base class enforcing
  institution ownership and auditing timestamps across all domain entities.
- Encapsulation: Methods compute active departments, student counts, and status checks.
"""

from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _


# Stored as #RRGGBB so the value can be emitted safely into a CSS custom
# property; anything else is rejected at the model layer.
HEX_COLOUR_VALIDATOR = RegexValidator(
    regex=r'^#[0-9a-fA-F]{6}$',
    message=_('Enter a hex colour in #RRGGBB format, e.g. #1F5F8B.'),
)


class Institution(models.Model):
    """
    Independent Higher Education Institution Tenant.
    All academic data, courses, students, and results are logically isolated by institution.
    """
    name = models.CharField(max_length=255, unique=True)
    code = models.CharField(max_length=20, unique=True, help_text=_("Short institution code, e.g. AUN, UNILAG, UI"))
    slug = models.SlugField(max_length=100, unique=True)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=30, blank=True, default='')
    address = models.TextField(blank=True, default='')
    website = models.URLField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    # Optional per-institution branding. Blank values keep the platform theme;
    # see accounts.context_processors.institution_branding for resolution.
    brand_primary_color = models.CharField(
        max_length=7,
        blank=True,
        default='',
        validators=[HEX_COLOUR_VALIDATOR],
        help_text=_('Primary brand colour as #RRGGBB, e.g. #12263F.'),
    )
    brand_accent_color = models.CharField(
        max_length=7,
        blank=True,
        default='',
        validators=[HEX_COLOUR_VALIDATOR],
        help_text=_('Accent colour for interactive highlights, as #RRGGBB.'),
    )
    logo_url = models.URLField(
        blank=True,
        default='',
        help_text=_(
            'Optional logo image URL. A file placed at '
            'static/branding/logo-<INSTITUTION-CODE>.svg is picked up '
            'automatically when this is empty.'
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Institution')
        verbose_name_plural = _('Institutions')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"

    def active_students_count(self):
        from accounts.models import Role, MembershipStatus
        return self.memberships.filter(role=Role.STUDENT, status=MembershipStatus.APPROVED).count()

    def active_instructors_count(self):
        from accounts.models import Role, MembershipStatus
        return self.memberships.filter(role=Role.INSTRUCTOR, status=MembershipStatus.APPROVED).count()


class TenantAwareModel(models.Model):
    """
    Abstract base class providing logical multi-tenancy isolation.
    Every entity subclassing this model is strictly bound to an Institution.
    """
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name='%(app_label)s_%(class)s_set')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantConsistencyMixin(models.Model):
    """
    Cross-foreign-key tenant validation for rows that reference other
    tenant-owned objects.

    A subclass lists, in ``TENANT_FK_FIELDS``, the foreign-key field names
    whose related row must belong to the same institution as the row itself.
    ``clean()`` raises a ``ValidationError`` naming the offending field, and
    ``save()`` enforces it so a mismatched row can never be persisted through
    the ORM — not only through the views.

    This converts tenant consistency from a seeding discipline (M-14) into a
    structural guarantee for the critical academic relations.
    """

    TENANT_FK_FIELDS = ()

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        for field_name in self.TENANT_FK_FIELDS:
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

    def save(self, *args, **kwargs):
        # Enforce the consistency rule at persistence time as well, so bulk
        # helpers, management commands and future importers cannot silently
        # create a cross-tenant row.
        self.clean()
        super().save(*args, **kwargs)


class Faculty(TenantAwareModel):
    """
    Faculty / College / School within an Institution.
    e.g., School of Computing & Information Technology.
    """
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20)
    description = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = _('Faculty')
        verbose_name_plural = _('Faculties')
        unique_together = ('institution', 'code')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} - {self.institution.code}"


class Department(TenantConsistencyMixin, TenantAwareModel):
    """
    Academic Department within a Faculty.
    e.g., Department of Software Engineering.
    """
    TENANT_FK_FIELDS = ('faculty',)

    faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name='departments')
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20)
    description = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = _('Department')
        verbose_name_plural = _('Departments')
        unique_together = ('institution', 'code')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class Programme(TenantConsistencyMixin, TenantAwareModel):
    """
    Degree Programme offered by a Department.
    e.g., B.Sc. Software Engineering.
    """
    TENANT_FK_FIELDS = ('department',)

    DEGREE_TYPES = [
        ('BSC', 'Bachelor of Science (B.Sc.)'),
        ('BENG', 'Bachelor of Engineering (B.Eng.)'),
        ('BA', 'Bachelor of Arts (B.A.)'),
        ('MSC', 'Master of Science (M.Sc.)'),
        ('PHD', 'Doctor of Philosophy (Ph.D.)'),
    ]

    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='programmes')
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20)
    degree_type = models.CharField(max_length=10, choices=DEGREE_TYPES, default='BSC')
    duration_years = models.PositiveSmallIntegerField(default=4)
    total_credits_required = models.PositiveIntegerField(default=120)

    class Meta:
        verbose_name = _('Academic Programme')
        verbose_name_plural = _('Academic Programmes')
        unique_together = ('institution', 'code')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"
