"""Academics URL configuration - EduTrack."""

from django.urls import path

from . import views

app_name = 'academics'

urlpatterns = [
    path('my-courses/', views.my_courses_view, name='my_courses'),
    path('instructor-courses/', views.instructor_courses_view, name='instructor_courses'),
    path('courses/', views.course_list_view, name='course_list'),
    path('courses/create/', views.course_create_view, name='course_create'),
    path('offerings/', views.offering_list_view, name='offering_list'),
    path('offerings/create/', views.offering_create_view, name='offering_create'),
    path('enrollments/', views.enrollment_list_view, name='enrollment_list'),
    path('enrollments/create/', views.enrollment_create_view, name='enrollment_create'),
    path('enrollments/<int:enrollment_id>/final-result/', views.final_result_view, name='final_result'),
    path('sessions/', views.session_list_view, name='session_list'),
    path('sessions/create/', views.session_create_view, name='session_create'),
    path('semesters/create/', views.semester_create_view, name='semester_create'),
    path('results/', views.registrar_results_view, name='registrar_results'),
]
