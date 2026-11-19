"""Institutions URL configuration - EduTrack."""

from django.urls import path

from . import views

app_name = 'institutions'

urlpatterns = [
    path('structure/', views.structure_view, name='structure_view'),
    path('structure/create/', views.structure_create_view, name='structure_create'),
    path('departments/', views.departments_view, name='departments'),
    path('programmes/', views.programmes_view, name='programmes'),
    path('', views.institution_list_view, name='institution_list'),
    path('create/', views.institution_create_view, name='institution_create'),
    path('requests/', views.institution_requests_view, name='institution_requests'),
    path('<int:institution_id>/toggle/', views.institution_toggle_view, name='institution_toggle'),
    path('<int:institution_id>/appoint-admin/', views.institution_admin_form_view, name='appoint_admin'),
    path('<int:institution_id>/branding/', views.institution_branding_view, name='institution_branding'),
]
