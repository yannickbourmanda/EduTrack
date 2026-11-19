"""Dashboard URL configuration - EduTrack."""

from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.home_dispatch_view, name='home'),
    path('student/', views.student_dashboard_view, name='student'),
    path('student/progress/', views.student_progress_view, name='student_progress'),
    path('instructor/', views.instructor_dashboard_view, name='instructor'),
    path('instructor/performance/', views.instructor_performance_view, name='instructor_performance'),
    path('instructor/students/', views.instructor_students_view, name='instructor_students'),
    path('adviser/', views.adviser_dashboard_view, name='adviser'),
    path('adviser/progress/', views.adviser_progress_view, name='adviser_progress'),
    path('registrar/', views.registrar_dashboard_view, name='registrar'),
    path('registrar/students/', views.registrar_students_view, name='registrar_students'),
    path('institution-admin/', views.institution_admin_dashboard_view, name='institution_admin'),
    path('institution-admin/reports/', views.institution_reports_view, name='reports'),
    path('platform-admin/', views.platform_admin_dashboard_view, name='platform_admin'),
    path('platform-admin/users/', views.platform_users_view, name='platform_users'),
    path('platform-admin/activity/', views.platform_activity_view, name='platform_activity'),
    path('audit-logs/', views.audit_logs_view, name='audit_logs'),
]
