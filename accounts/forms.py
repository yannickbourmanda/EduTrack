"""
Accounts Forms - EduTrack
Forms for user registration, login, profile updates, and institutional membership requests.
"""

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from .models import User, Membership, Role, MembershipStatus
from institutions.models import Institution, Department, Programme


class UserRegistrationForm(forms.ModelForm):
    """
    Registration form collecting user personal details, institution selection,
    and requested institutional role.
    """
    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Enter a secure password'}),
        help_text=_("At least 8 characters. Cannot be too common, purely numeric, or too similar to your name or email.")
    )
    confirm_password = forms.CharField(
        label=_("Confirm Password"),
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Re-enter your password'})
    )
    institution = forms.ModelChoiceField(
        queryset=Institution.objects.filter(is_active=True),
        label=_("Institution / University"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    requested_role = forms.ChoiceField(
        choices=[
            (Role.STUDENT, _('Student')),
            (Role.INSTRUCTOR, _('Instructor / Lecturer')),
            (Role.ADVISER, _('Academic Adviser')),
            (Role.REGISTRAR, _('Registrar Staff')),
            (Role.INSTITUTION_ADMIN, _('Institution Administrator')),
        ],
        label=_("Requested Role"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    id_number = forms.CharField(
        max_length=50,
        required=False,
        label=_("Matriculation / Staff ID"),
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. A00012345'})
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone_number']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First name'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last name'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'name@example.edu'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+234...'}),
        }

    def clean_email(self):
        email = self.cleaned_data.get('email', '').lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(_("A user with this email address already exists."))
        return email

    def clean(self):
        cleaned_data = super().clean()
        pwd = cleaned_data.get('password')
        cpwd = cleaned_data.get('confirm_password')

        if pwd and cpwd and pwd != cpwd:
            self.add_error('confirm_password', _("Passwords do not match."))

        if pwd:
            # Validate against Django's configured AUTH_PASSWORD_VALIDATORS
            # (minimum length, common-password and numeric-only rules). A
            # transient user built from the cleaned attributes lets the
            # similarity validator compare against the name/email supplied.
            candidate = User(
                username=cleaned_data.get('email') or '',
                email=cleaned_data.get('email') or '',
                first_name=cleaned_data.get('first_name') or '',
                last_name=cleaned_data.get('last_name') or '',
            )
            try:
                validate_password(pwd, candidate)
            except ValidationError as exc:
                self.add_error('password', exc)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data['email']
        user.set_password(self.cleaned_data['password'])
        if self.cleaned_data.get('id_number'):
            if self.cleaned_data['requested_role'] == Role.STUDENT:
                user.matriculation_number = self.cleaned_data['id_number']
            else:
                user.staff_id = self.cleaned_data['id_number']
        if commit:
            user.save()
        return user


class LoginForm(forms.Form):
    """
    Standard email and password login form.
    """
    email = forms.EmailField(
        label=_("Email Address"),
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'name@example.edu', 'autofocus': True})
    )
    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': '••••••••'})
    )


class MembershipReviewForm(forms.ModelForm):
    """
    Form used by Institution Admin to approve or reject a membership request,
    with optional department/programme assignment.
    """
    class Meta:
        model = Membership
        fields = ['role', 'status', 'department', 'programme', 'rejection_reason']
        widgets = {
            'role': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'department': forms.Select(attrs={'class': 'form-select'}),
            'programme': forms.Select(attrs={'class': 'form-select'}),
            'rejection_reason': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Provide reason if rejected'}),
        }
