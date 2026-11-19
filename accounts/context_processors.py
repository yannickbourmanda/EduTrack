"""
Template context processors for EduTrack.

Expose the active membership, institution, role and platform-administrator flag
to every template. These values are presentation only; server-side permissions
remain authoritative.

Memberships at deactivated institutions are excluded so that neither the
sidebar nor the institution switcher offers a tenant that no longer serves
records. The resolved active membership is shared with
``accounts.permissions.get_active_membership`` via a per-request cache.
"""

from .permissions import (
    approved_institution_memberships,
    get_active_institution,
    user_is_platform_admin,
)


def user_membership_context(request):
    if not request.user.is_authenticated:
        return {
            'active_membership': None,
            'active_institution': None,
            'user_role': None,
            'user_memberships': [],
            'is_platform_admin': False,
        }

    memberships = list(approved_institution_memberships(request.user))

    active_membership_id = request.session.get('active_membership_id')
    active_membership = next(
        (m for m in memberships if m.id == active_membership_id), None
    )
    if not active_membership and memberships:
        active_membership = memberships[0]
        request.session['active_membership_id'] = active_membership.id
    # Share the resolution with accounts.permissions.get_active_membership so
    # it is not queried twice within the same request.
    request._active_membership_cache = active_membership

    platform_admin = user_is_platform_admin(request.user)
    role = active_membership.role if active_membership else (
        'PLATFORM_ADMIN' if platform_admin else None
    )

    return {
        'active_membership': active_membership,
        'active_institution': active_membership.institution if active_membership else None,
        'user_role': role,
        'user_memberships': memberships,
        'is_platform_admin': platform_admin,
    }


def institution_branding(request):
    """
    Resolve the signed-in user's institutional branding for templates.

    Resolution order for the logo:
      1. the institution's explicit ``logo_url``;
      2. the static convention ``static/branding/logo-<INSTITUTION-CODE>.svg``
         (a file dropped into the project needs no code change);
      3. an honest monogram placeholder rendered by the template until the
         institution supplies its official asset — no logo is ever fabricated.

    Colour overrides are emitted as CSS custom properties on the app shell;
    they are stored in strict ``#RRGGBB`` form (validated on the model) and
    blank values keep the platform default theme. Platform administrators
    deliberately keep the neutral EduTrack platform identity, and anonymous
    users (sign-in and other public pages) never see institutional branding.
    """
    branding = {
        'institution': None,
        'logo_url': '',
        'monogram': '',
        'primary': '',
        'accent': '',
    }

    if not request.user.is_authenticated or user_is_platform_admin(request.user):
        return {'institution_branding': branding}

    institution = get_active_institution(request)
    if institution is None:
        return {'institution_branding': branding}

    from django.contrib.staticfiles import finders
    from django.templatetags.static import static

    logo_url = institution.logo_url or ''
    if not logo_url:
        candidate = f'branding/logo-{institution.code}.svg'
        if finders.find(candidate):
            logo_url = static(candidate)

    branding.update({
        'institution': institution,
        'logo_url': logo_url,
        'monogram': institution.code[:3].upper(),
        'primary': institution.brand_primary_color or '',
        'accent': institution.brand_accent_color or '',
    })
    return {'institution_branding': branding}
