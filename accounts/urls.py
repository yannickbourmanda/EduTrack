"""Accounts URL configuration - EduTrack."""

from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = 'accounts'

urlpatterns = [
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('pending-approval/', views.pending_approval_view, name='pending_approval'),
    path('switch-institution/<int:membership_id>/', views.switch_institution_view, name='switch_institution'),
    path('memberships/', views.membership_management_view, name='membership_management'),
    path('memberships/<int:membership_id>/approve/', views.approve_membership_view, name='approve_membership'),
    path('memberships/<int:membership_id>/reject/', views.reject_membership_view, name='reject_membership'),
    path('memberships/<int:membership_id>/suspend/', views.suspend_membership_view, name='suspend_membership'),
    path('memberships/<int:membership_id>/reactivate/', views.reactivate_membership_view, name='reactivate_membership'),
    path('memberships/<int:membership_id>/role/', views.change_membership_role_view, name='change_membership_role'),
    path('profile/', views.profile_view, name='profile'),

    # Password management (Django's built-in views with EduTrack templates).
    path(
        'password/',
        views.AuditedPasswordChangeView.as_view(
            template_name='registration/password_change_form.html',
            success_url='/accounts/password/done/',
        ),
        name='password_change',
    ),
    path(
        'password/done/',
        auth_views.PasswordChangeDoneView.as_view(
            template_name='registration/password_change_done.html',
        ),
        name='password_change_done',
    ),
    path(
        'password-reset/',
        views.AuditedPasswordResetView.as_view(
            template_name='registration/password_reset_form.html',
            email_template_name='registration/password_reset_email.txt',
            subject_template_name='registration/password_reset_subject.txt',
            success_url='/accounts/password-reset/done/',
        ),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='registration/password_reset_done.html',
        ),
        name='password_reset_done',
    ),
    path(
        'password-reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='registration/password_reset_confirm.html',
            success_url='/accounts/password-reset/complete/',
        ),
        name='password_reset_confirm',
    ),
    path(
        'password-reset/complete/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='registration/password_reset_complete.html',
        ),
        name='password_reset_complete',
    ),
]
