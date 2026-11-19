"""
Monitoring URL Configuration - EduTrack
"""

from django.urls import path
from . import views

app_name = 'monitoring'

urlpatterns = [
    path('assessments/', views.assessment_management_view, name='assessment_management'),
    path('assessments/create/', views.create_assessment_view, name='create_assessment'),
    path('assessments/<int:assessment_id>/grade/', views.grade_assessment_view, name='grade_assessment'),
    path('offerings/<int:offering_id>/scores/', views.enter_scores_view, name='enter_scores'),
    path('attendance/', views.attendance_overview_view, name='attendance_overview'),
    path('offerings/<int:offering_id>/attendance/', views.record_attendance_view, name='record_attendance'),
    path('my-grades/', views.my_grades_view, name='my_grades'),
    path('my-attendance/', views.my_attendance_view, name='my_attendance'),
]
