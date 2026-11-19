"""
Advising URL Configuration - EduTrack
"""

from django.urls import path
from . import views

app_name = 'advising'

urlpatterns = [
    path('students/', views.assigned_students_view, name='assigned_students'),
    path('assign-adviser/', views.assign_adviser_view, name='assign_adviser'),
    path('interventions/', views.intervention_list_view, name='intervention_list'),
    path('interventions/create/', views.create_intervention_view, name='create_intervention'),
    path('interventions/create/<int:student_id>/', views.create_intervention_view, name='create_intervention_for_student'),
    path('interventions/<int:intervention_id>/', views.intervention_detail_view, name='intervention_detail'),
]
