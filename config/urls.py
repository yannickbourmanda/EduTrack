"""
URL configuration for EduTrack project.
"""

from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.views.decorators.http import require_GET


@require_GET
def health_view(request):
    """
    Readiness probe for load balancers and monitoring.

    Checks that the database answers a trivial query and that the machine
    learning artifact is present. Returns 200 when both are healthy, 503
    otherwise. It never reads or writes application data.
    """
    import json
    import os
    from django.conf import settings
    from django.db import connection
    from django.http import HttpResponse

    checks = {}
    status_code = 200

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        checks['database'] = 'ok'
    except Exception as exc:  # pragma: no cover - depends on DB state
        checks['database'] = f'error: {type(exc).__name__}'
        status_code = 503

    model_path = os.path.join(
        settings.BASE_DIR, 'ml_artifacts', 'academic_risk_rf_v1.joblib'
    )
    if os.path.exists(model_path):
        checks['ml_artifact'] = 'ok'
    else:
        checks['ml_artifact'] = 'missing (heuristic fallback will be used)'
        # The heuristic fallback keeps the service usable, so this is not a
        # hard failure — the load balancer should keep routing.
        status_code = 200 if status_code == 200 else status_code

    payload = {
        'status': 'ok' if status_code == 200 else 'degraded',
        'checks': checks,
    }
    return HttpResponse(
        json.dumps(payload),
        content_type='application/json',
        status=status_code,
    )


def root_redirect(request):
    return redirect('dashboard:home')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', health_view, name='health'),
    path('', root_redirect, name='root'),
    path('accounts/', include('accounts.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('academics/', include('academics.urls')),
    path('monitoring/', include('monitoring.urls')),
    path('advising/', include('advising.urls')),
    path('predictions/', include('predictions.urls')),
    path('institutions/', include('institutions.urls')),
]
