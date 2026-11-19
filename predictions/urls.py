"""
Predictions URL Configuration - EduTrack
"""

from django.urls import path
from . import views

app_name = 'predictions'

urlpatterns = [
    path('my-risk/', views.my_risk_view, name='my_risk'),
    path('overview/', views.risk_overview_view, name='risk_overview'),
    path('metrics/', views.model_metrics_view, name='model_metrics'),
    path('evaluate/<int:student_id>/', views.trigger_prediction_view, name='evaluate_student'),
]
