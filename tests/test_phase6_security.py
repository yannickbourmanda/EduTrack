"""
Phase 6 security and integrity tests - EduTrack.

Regression coverage for the remediated security work: the sign-in brute-force
guard, password change and reset flows with their audit trails, the membership
lifecycle (suspend / reactivate / role change with a last-administrator
guard), the platform's first-administrator appointment flow, registrar
workflows scoped to the active tenant, the database constraints that protect
academic integrity, the readiness probe, and per-dashboard query budgets that
act as N+1 regression alarms.
"""

from datetime import date

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import Membership, MembershipStatus, Role, User
from academics.models import (
    AcademicSession,
    Course,
    CourseOffering,
    Enrollment,
    Semester,
)
from advising.models import AdviserAssignment
from dashboard.models import AuditLog
from institutions.models import Department, Faculty, Institution
from monitoring.models import (
    Assessment,
    AssessmentResult,
    AssessmentType,
    AttendanceRecord,
    AttendanceStatus,
    ClassSession,
    ParticipationRecord,
)
from predictions.models import RiskLevel, RiskPrediction

PASSWORD = 'Password123!'
NEW_PASSWORD = 'R3placement!Passphrase'

# Measured per-dashboard query counts for the Phase 6 build (see
# DashboardQueryBudgetTests). A regression that adds queries to a dashboard
# fails these tests before it can reach production.
STUDENT_DASHBOARD_QUERIES = 19
STUDENT_PROGRESS_QUERIES = 14
INSTRUCTOR_DASHBOARD_QUERIES = 16
INSTRUCTOR_PERFORMANCE_QUERIES = 15
ADVISER_DASHBOARD_QUERIES = 20
ADVISER_PROGRESS_QUERIES = 18


class Phase6FixtureMixin:
    """Tenants, users and baseline academic records for the Phase 6 tests."""

    @classmethod
    def setUpTestData(cls):
        # Primary tenant with a full staff set.
        cls.inst_a = Institution.objects.create(
            name='American University of Nigeria',
            code='AUN',
            slug='aun',
            contact_email='info@aun.edu.ng',
        )
        # Second tenant with staff, used for tenant-scoping assertions.
        cls.inst_c = Institution.objects.create(
            name='University of Lagos',
            code='UNILAG',
            slug='unilag',
            contact_email='info@unilag.edu.ng',
        )
        # Third tenant with NO administrator: the platform must be able to
        # appoint one, and only once.
        cls.inst_b = Institution.objects.create(
            name='Bells University of Technology',
            code='BELLS',
            slug='bells',
            contact_email='info@bells.edu.ng',
        )

        cls.platform = cls._user('platform.admin@edutrack.edu', 'Aliyu', 'Mohammed')
        cls.platform.is_superuser = True
        cls.platform.save()

        cls.admin_a = cls._user('admin.a@aun.edu.ng', 'Amina', 'Bello')
        cls.admin2_a = cls._user('admin.two@aun.edu.ng', 'Baba', 'Second')
        cls.registrar_a = cls._user('registrar.a@aun.edu.ng', 'Rita', 'Okafor')
        cls.instructor_a = cls._user('instructor.a@aun.edu.ng', 'Ibrahim', 'Musa')
        cls.adviser_a = cls._user('adviser.a@aun.edu.ng', 'Chioma', 'Nnamdi')
        cls.student_a = cls._user('student.a@aun.edu.ng', 'Emmanuel', 'Okafor')
        cls.student_b = cls._user('student.b@aun.edu.ng', 'Folake', 'Adeyemi')

        cls.admin_c = cls._user('admin.c@unilag.edu.ng', 'Adeyemi', 'Lawal')
        cls.registrar_c = cls._user('registrar.c@unilag.edu.ng', 'Ngozi', 'Unilag')
        cls.student_c = cls._user('student.c@unilag.edu.ng', 'Tunde', 'Unilag')

        cls.candidate_b = cls._user('candidate@bells.edu.ng', 'Chidi', 'Bells')

        cls._member(cls.admin_a, cls.inst_a, Role.INSTITUTION_ADMIN)
        cls._member(cls.registrar_a, cls.inst_a, Role.REGISTRAR)
        cls._member(cls.instructor_a, cls.inst_a, Role.INSTRUCTOR)
        cls._member(cls.adviser_a, cls.inst_a, Role.ADVISER)
        cls._member(cls.student_a, cls.inst_a, Role.STUDENT)
        cls._member(cls.student_b, cls.inst_a, Role.STUDENT)

        cls._member(cls.admin_c, cls.inst_c, Role.INSTITUTION_ADMIN)
        cls._member(cls.registrar_c, cls.inst_c, Role.REGISTRAR)
        cls._member(cls.student_c, cls.inst_c, Role.STUDENT)

        cls.faculty_a = Faculty.objects.create(
            institution=cls.inst_a, name='Computing', code='SIT'
        )
        cls.dept_a = Department.objects.create(
            institution=cls.inst_a,
            faculty=cls.faculty_a,
            name='Software Engineering',
            code='SE',
        )
        cls.session_a = AcademicSession.objects.create(
            institution=cls.inst_a,
            name='2025/2026',
            start_date=date(2025, 9, 1),
            end_date=date(2026, 6, 30),
            is_current=True,
        )
        cls.semester_a = Semester.objects.create(
            institution=cls.inst_a,
            session=cls.session_a,
            name='First Semester',
            start_date=date(2025, 9, 8),
            end_date=date(2026, 1, 20),
            is_current=True,
        )
        cls.course_a = Course.objects.create(
            institution=cls.inst_a,
            department=cls.dept_a,
            code='CSC301',
            title='Object-Oriented Software Construction',
            credit_units=3,
        )
        cls.offering_a = CourseOffering.objects.create(
            institution=cls.inst_a,
            course=cls.course_a,
            session=cls.session_a,
            semester=cls.semester_a,
            instructor=cls.instructor_a,
        )
        cls.enrollment_a = Enrollment.objects.create(
            institution=cls.inst_a,
            student=cls.student_a,
            course_offering=cls.offering_a,
        )

        # Representative evidence so the dashboards render populated rows.
        cls.quiz_a = Assessment.objects.create(
            institution=cls.inst_a,
            course_offering=cls.offering_a,
            title='Quiz 1',
            assessment_type=AssessmentType.QUIZ,
            max_score=20.0,
        )
        cls.midterm_a = Assessment.objects.create(
            institution=cls.inst_a,
            course_offering=cls.offering_a,
            title='Midterm',
            assessment_type=AssessmentType.MIDTERM,
            max_score=100.0,
        )
        cls.quiz_result_a = AssessmentResult.objects.create(
            institution=cls.inst_a,
            assessment=cls.quiz_a,
            student=cls.student_a,
            score=18.0,
        )
        cls.class_session_a = ClassSession.objects.create(
            institution=cls.inst_a,
            course_offering=cls.offering_a,
            session_date=date(2025, 9, 15),
            topic='Introduction',
        )
        cls.attendance_a = AttendanceRecord.objects.create(
            institution=cls.inst_a,
            class_session=cls.class_session_a,
            student=cls.student_a,
            status=AttendanceStatus.PRESENT,
        )
        cls.participation_a = ParticipationRecord.objects.create(
            institution=cls.inst_a,
            class_session=cls.class_session_a,
            student=cls.student_a,
            score=4,
        )
        cls.assignment_a = AdviserAssignment.objects.create(
            institution=cls.inst_a,
            adviser=cls.adviser_a,
            student=cls.student_a,
        )

        cls.student_membership_a = Membership.objects.get(
            user=cls.student_a, institution=cls.inst_a
        )
        cls.admin_membership_a = Membership.objects.get(
            user=cls.admin_a, institution=cls.inst_a
        )

    @staticmethod
    def _user(email, first_name, last_name):
        return User.objects.create_user(
            username=email,
            email=email,
            password=PASSWORD,
            first_name=first_name,
            last_name=last_name,
        )

    @staticmethod
    def _member(user, institution, role, status=MembershipStatus.APPROVED):
        return Membership.objects.create(
            user=user,
            institution=institution,
            role=role,
            status=status,
        )

    def setUp(self):
        # The sign-in throttle counters live in the shared cache; reset them
        # so tests never inherit (or poison) each other's lockout state.
        cache.clear()


class LoginThrottleTests(Phase6FixtureMixin, TestCase):
    """The brute-force guard locks the email after five failed attempts."""

    def _attempt(self, email, password):
        return self.client.post(reverse('accounts:login'), {
            'email': email,
            'password': password,
        })

    def test_five_failures_lock_even_correct_password(self):
        for _ in range(5):
            response = self._attempt(self.student_a.email, 'WrongPassword1!')
            self.assertEqual(response.status_code, 200)

        # The sixth attempt, now with the CORRECT password, is refused.
        response = self._attempt(self.student_a.email, PASSWORD)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Too many failed sign-in attempts')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_failures_are_audited_with_client_ip(self):
        for _ in range(5):
            self._attempt(self.student_a.email, 'WrongPassword1!')
        self._attempt(self.student_a.email, PASSWORD)  # refused by the lock

        failed = AuditLog.objects.filter(
            action=AuditLog.ActionChoices.LOGIN_FAILED
        )
        # Five counted failures plus one "lockout active" refusal.
        self.assertEqual(failed.count(), 6)
        self.assertTrue(
            failed.filter(ip_address='127.0.0.1').exists(),
            'Failed sign-in audit rows must record the client address.',
        )
        self.assertTrue(
            failed.filter(description__icontains='lockout active').exists(),
            'The refusal itself must be audited.',
        )

    def test_failures_before_the_limit_do_not_block_sign_in(self):
        self._attempt(self.student_a.email, 'WrongPassword1!')
        self._attempt(self.student_a.email, 'WrongPassword2!')
        response = self._attempt(self.student_a.email, PASSWORD)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('dashboard:home'), response['Location'])


class PasswordChangeTests(Phase6FixtureMixin, TestCase):
    """The signed-in password change updates credentials and audits itself."""

    def test_password_change_updates_credentials_and_audits(self):
        self.client.force_login(self.student_a)
        response = self.client.post(reverse('accounts:password_change'), {
            'old_password': PASSWORD,
            'new_password1': NEW_PASSWORD,
            'new_password2': NEW_PASSWORD,
        })
        self.assertRedirects(response, reverse('accounts:password_change_done'))

        self.student_a.refresh_from_db()
        self.assertTrue(self.student_a.check_password(NEW_PASSWORD))
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.ActionChoices.PASSWORD_CHANGED,
                institution=self.inst_a,
                user=self.student_a,
            ).exists(),
            'A successful password change must be audited.',
        )

        self.client.logout()
        self.assertFalse(
            self.client.login(email=self.student_a.email, password=PASSWORD),
            'The old password must stop working after the change.',
        )
        self.assertTrue(
            self.client.login(email=self.student_a.email, password=NEW_PASSWORD)
        )

    def test_wrong_old_password_changes_nothing(self):
        self.client.force_login(self.student_a)
        response = self.client.post(reverse('accounts:password_change'), {
            'old_password': 'WrongOld!Passphrase',
            'new_password1': NEW_PASSWORD,
            'new_password2': NEW_PASSWORD,
        })
        self.assertEqual(response.status_code, 200)
        self.student_a.refresh_from_db()
        self.assertTrue(self.student_a.check_password(PASSWORD))
        self.assertFalse(
            AuditLog.objects.filter(
                action=AuditLog.ActionChoices.PASSWORD_CHANGED
            ).exists()
        )


class PasswordResetTests(Phase6FixtureMixin, TestCase):
    """The signed-out reset flow issues a link and audits the request."""

    def _request_reset(self, email):
        return self.client.post(reverse('accounts:password_reset'), {
            'email': email,
        })

    def _confirm_url(self, user):
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        return reverse('accounts:password_reset_confirm', args=[uid, token])

    def test_full_reset_flow_replaces_password(self):
        response = self._request_reset(self.student_a.email)
        self.assertRedirects(response, reverse('accounts:password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.student_a.email])
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.ActionChoices.PASSWORD_RESET_REQUESTED
            ).exists(),
            'A reset request must be audited.',
        )

        # A valid token is stored in the session and the browser is
        # redirected to a token-free URL before the form is rendered.
        confirm_url = self._confirm_url(self.student_a)
        response = self.client.get(confirm_url)
        self.assertEqual(response.status_code, 302)
        form_url = response['Location']
        self.assertEqual(self.client.get(form_url).status_code, 200)

        response = self.client.post(form_url, {
            'new_password1': NEW_PASSWORD,
            'new_password2': NEW_PASSWORD,
        })
        self.assertRedirects(response, reverse('accounts:password_reset_complete'))

        self.student_a.refresh_from_db()
        self.assertTrue(self.student_a.check_password(NEW_PASSWORD))
        self.assertFalse(
            self.client.login(email=self.student_a.email, password=PASSWORD),
            'The pre-reset password must stop working.',
        )
        self.assertTrue(
            self.client.login(email=self.student_a.email, password=NEW_PASSWORD)
        )

    def test_reset_rejects_policy_violating_password(self):
        self._request_reset(self.student_a.email)
        response = self.client.get(self._confirm_url(self.student_a))
        form_url = response['Location']
        response = self.client.post(form_url, {
            'new_password1': '12345678',
            'new_password2': '12345678',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'entirely numeric')
        self.student_a.refresh_from_db()
        self.assertTrue(self.student_a.check_password(PASSWORD))

    def test_unknown_email_gets_no_email_and_no_information(self):
        response = self._request_reset('nobody@nowhere.edu')
        self.assertRedirects(response, reverse('accounts:password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)


class MembershipLifecycleTests(Phase6FixtureMixin, TestCase):
    """Suspend / reactivate / role change, with administrator protections."""

    def test_suspend_member_and_lock_their_access(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(
            reverse('accounts:suspend_membership',
                    args=[self.student_membership_a.id])
        )
        self.assertRedirects(response, reverse('accounts:membership_management'))

        self.student_membership_a.refresh_from_db()
        self.assertEqual(self.student_membership_a.status, MembershipStatus.SUSPENDED)
        self.assertEqual(self.student_membership_a.reviewed_by, self.admin_a)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.ActionChoices.MEMBERSHIP_SUSPENDED,
                institution=self.inst_a,
            ).exists()
        )

        fresh = Client()
        fresh.force_login(self.student_a)
        response = fresh.get(reverse('dashboard:student'))
        self.assertRedirects(response, reverse('accounts:pending_approval'))

    def test_reactivate_suspended_member(self):
        self.student_membership_a.status = MembershipStatus.SUSPENDED
        self.student_membership_a.save(update_fields=['status'])

        self.client.force_login(self.admin_a)
        response = self.client.post(
            reverse('accounts:reactivate_membership',
                    args=[self.student_membership_a.id])
        )
        self.assertRedirects(response, reverse('accounts:membership_management'))

        self.student_membership_a.refresh_from_db()
        self.assertEqual(self.student_membership_a.status, MembershipStatus.APPROVED)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.ActionChoices.MEMBERSHIP_REACTIVATED,
                institution=self.inst_a,
            ).exists()
        )

    def test_role_change_updates_role_and_audits(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(
            reverse('accounts:change_membership_role',
                    args=[self.student_membership_a.id]),
            {'role': Role.INSTRUCTOR},
        )
        self.assertRedirects(response, reverse('accounts:membership_management'))

        self.student_membership_a.refresh_from_db()
        self.assertEqual(self.student_membership_a.role, Role.INSTRUCTOR)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.ActionChoices.ROLE_CHANGED,
                institution=self.inst_a,
                description__icontains=self.student_a.email,
            ).exists()
        )

    def test_invalid_role_is_rejected(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(
            reverse('accounts:change_membership_role',
                    args=[self.student_membership_a.id]),
            {'role': 'SUPERSTAR'},
        )
        self.assertRedirects(response, reverse('accounts:membership_management'))
        self.student_membership_a.refresh_from_db()
        self.assertEqual(self.student_membership_a.role, Role.STUDENT)

    def test_administrator_cannot_suspend_own_account(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(
            reverse('accounts:suspend_membership', args=[self.admin_membership_a.id])
        )
        self.assertRedirects(response, reverse('accounts:membership_management'))
        self.admin_membership_a.refresh_from_db()
        self.assertEqual(self.admin_membership_a.status, MembershipStatus.APPROVED)

    def test_last_administrator_cannot_be_removed(self):
        """The sole active administrator cannot be demoted or outlived."""
        self.client.force_login(self.admin_a)

        # With admin_a as the only administrator, a role change is refused.
        response = self.client.post(
            reverse('accounts:change_membership_role',
                    args=[self.admin_membership_a.id]),
            {'role': Role.REGISTRAR},
        )
        self.assertRedirects(response, reverse('accounts:membership_management'))
        self.admin_membership_a.refresh_from_db()
        self.assertEqual(self.admin_membership_a.role, Role.INSTITUTION_ADMIN)

        # A second administrator can be suspended by the first one.
        self._member(self.admin2_a, self.inst_a, Role.INSTITUTION_ADMIN)
        second = Membership.objects.get(user=self.admin2_a, institution=self.inst_a)
        response = self.client.post(
            reverse('accounts:suspend_membership', args=[second.id])
        )
        second.refresh_from_db()
        self.assertEqual(second.status, MembershipStatus.SUSPENDED)

        # But with the second one suspended, admin_a is again the last
        # administrator and cannot be demoted.
        response = self.client.post(
            reverse('accounts:change_membership_role',
                    args=[self.admin_membership_a.id]),
            {'role': Role.REGISTRAR},
        )
        self.admin_membership_a.refresh_from_db()
        self.assertEqual(self.admin_membership_a.role, Role.INSTITUTION_ADMIN)
        self.assertFalse(
            AuditLog.objects.filter(
                action=AuditLog.ActionChoices.ROLE_CHANGED,
                description__icontains=self.admin_a.email,
            ).exists()
        )

    def test_only_approved_members_can_be_suspended(self):
        pending = self._member(
            self._user('applicant@aun.edu.ng', 'Ada', 'Applicant'),
            self.inst_a,
            Role.STUDENT,
            MembershipStatus.PENDING,
        )
        self.client.force_login(self.admin_a)
        response = self.client.post(
            reverse('accounts:suspend_membership', args=[pending.id])
        )
        self.assertRedirects(response, reverse('accounts:membership_management'))
        pending.refresh_from_db()
        self.assertEqual(pending.status, MembershipStatus.PENDING)

    def test_membership_actions_are_scoped_to_own_institution(self):
        other = Membership.objects.get(user=self.student_c, institution=self.inst_c)
        self.client.force_login(self.admin_a)
        for url_name in (
            'accounts:suspend_membership',
            'accounts:reactivate_membership',
            'accounts:change_membership_role',
        ):
            with self.subTest(view=url_name):
                response = self.client.post(
                    reverse(url_name, args=[other.id]),
                    {'role': Role.REGISTRAR},
                )
                self.assertEqual(response.status_code, 404)
        other.refresh_from_db()
        self.assertEqual(other.status, MembershipStatus.APPROVED)

    def test_registrar_cannot_manage_memberships(self):
        self.client.force_login(self.registrar_a)
        response = self.client.post(
            reverse('accounts:suspend_membership',
                    args=[self.student_membership_a.id])
        )
        self.assertEqual(response.status_code, 403)
        self.student_membership_a.refresh_from_db()
        self.assertEqual(self.student_membership_a.status, MembershipStatus.APPROVED)

    def test_membership_actions_require_post(self):
        self.client.force_login(self.admin_a)
        for url_name in (
            'accounts:suspend_membership',
            'accounts:reactivate_membership',
            'accounts:change_membership_role',
        ):
            with self.subTest(view=url_name):
                response = self.client.get(
                    reverse(url_name, args=[self.student_membership_a.id])
                )
                self.assertEqual(response.status_code, 405)


class AppointAdministratorTests(Phase6FixtureMixin, TestCase):
    """The platform appoints the FIRST administrator of a tenant, once."""

    def test_platform_admin_can_appoint_first_administrator(self):
        self.assertFalse(
            Membership.objects.filter(
                institution=self.inst_b, role=Role.INSTITUTION_ADMIN
            ).exists()
        )
        self.client.force_login(self.platform)

        response = self.client.get(
            reverse('institutions:appoint_admin', args=[self.inst_b.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.candidate_b.email)

        response = self.client.post(
            reverse('institutions:appoint_admin', args=[self.inst_b.id]),
            {'user': str(self.candidate_b.id)},
        )
        self.assertRedirects(response, reverse('institutions:institution_list'))

        membership = Membership.objects.get(
            user=self.candidate_b, institution=self.inst_b
        )
        self.assertEqual(membership.role, Role.INSTITUTION_ADMIN)
        self.assertEqual(membership.status, MembershipStatus.APPROVED)
        self.assertEqual(membership.reviewed_by, self.platform)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.ActionChoices.INSTITUTION_ADMIN_APPOINTED,
                institution=self.inst_b,
            ).exists()
        )

    def test_appointment_refused_when_admin_already_exists(self):
        self._member(self.candidate_b, self.inst_b, Role.INSTITUTION_ADMIN)
        self.client.force_login(self.platform)

        response = self.client.get(
            reverse('institutions:appoint_admin', args=[self.inst_b.id])
        )
        self.assertRedirects(response, reverse('institutions:institution_list'))

        response = self.client.post(
            reverse('institutions:appoint_admin', args=[self.inst_b.id]),
            {'user': str(self.student_c.id)},
        )
        self.assertRedirects(response, reverse('institutions:institution_list'))
        self.assertEqual(
            Membership.objects.filter(
                institution=self.inst_b, role=Role.INSTITUTION_ADMIN
            ).count(),
            1,
            'No second administrator may be appointed from the platform.',
        )
        self.assertFalse(
            Membership.objects.filter(user=self.student_c, institution=self.inst_b).exists()
        )

    def test_institution_admin_cannot_appoint_administrators(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(
            reverse('institutions:appoint_admin', args=[self.inst_b.id])
        )
        self.assertEqual(response.status_code, 403)


class RegistrarWorkflowTests(Phase6FixtureMixin, TestCase):
    """Registrar writes stay inside the active tenant and are audited."""

    def test_session_create_is_scoped_and_audited(self):
        self.client.force_login(self.registrar_a)
        response = self.client.post(reverse('academics:session_create'), {
            'name': '2026/2027',
            'start_date': '2026-09-01',
            'end_date': '2027-06-30',
        })
        self.assertRedirects(response, reverse('academics:session_list'))

        session = AcademicSession.objects.get(
            institution=self.inst_a, name='2026/2027'
        )
        self.assertEqual(session.start_date, date(2026, 9, 1))
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.ActionChoices.SESSION_CREATED,
                institution=self.inst_a,
            ).exists()
        )

    def test_final_result_recorded_and_audited(self):
        self.client.force_login(self.registrar_a)
        response = self.client.post(
            reverse('academics:final_result', args=[self.enrollment_a.id]),
            {'final_score': '75.5', 'final_grade': 'B', 'grade_point': '3.0'},
        )
        self.assertRedirects(response, reverse('academics:registrar_results'))

        self.enrollment_a.refresh_from_db()
        self.assertAlmostEqual(float(self.enrollment_a.final_score), 75.5)
        self.assertEqual(self.enrollment_a.final_grade, 'B')
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.ActionChoices.FINAL_RESULT_RECORDED,
                institution=self.inst_a,
            ).exists()
        )

    def test_final_result_of_another_tenant_is_not_reachable(self):
        self.client.force_login(self.registrar_c)
        url = reverse('academics:final_result', args=[self.enrollment_a.id])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.post(url, {'final_score': '10', 'final_grade': 'F'}).status_code,
            404,
        )
        self.enrollment_a.refresh_from_db()
        self.assertIsNone(self.enrollment_a.final_score)

    def test_enrollment_create_cannot_cross_tenants(self):
        self.client.force_login(self.registrar_c)
        response = self.client.post(reverse('academics:enrollment_create'), {
            'student': str(self.student_a.id),
            'course_offering': str(self.offering_a.id),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select an enrolled student')
        # Nothing may be written into the other tenant, and the existing
        # enrolment in inst_a stays untouched.
        self.assertFalse(
            Enrollment.objects.filter(institution=self.inst_c).exists()
        )
        self.assertEqual(
            Enrollment.objects.filter(
                student=self.student_a, course_offering=self.offering_a
            ).count(),
            1,
        )

    def test_registrar_results_only_show_own_tenant(self):
        self.client.force_login(self.registrar_c)
        response = self.client.get(reverse('academics:registrar_results'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['result_rows']), 0)
        self.assertNotIn('CSC301', response.content.decode())


class DatabaseIntegrityConstraintTests(Phase6FixtureMixin, TestCase):
    """Database constraints are the last line of defence for record quality."""

    def test_duplicate_assessment_title_per_offering_blocked(self):
        first = Assessment.objects.create(
            institution=self.inst_a,
            course_offering=self.offering_a,
            title='Quiz 2',
            assessment_type=AssessmentType.QUIZ,
            max_score=20.0,
        )
        self.assertEqual(first.title, 'Quiz 2')
        with self.assertRaises(IntegrityError):
            Assessment.objects.create(
                institution=self.inst_a,
                course_offering=self.offering_a,
                title='Quiz 2',
                assessment_type=AssessmentType.QUIZ,
                max_score=20.0,
            )

    def test_duplicate_class_session_same_day_blocked(self):
        first = ClassSession.objects.create(
            institution=self.inst_a,
            course_offering=self.offering_a,
            session_date=date(2025, 9, 22),
            topic='Week 2',
        )
        self.assertIsNotNone(first.id)
        with self.assertRaises(IntegrityError):
            ClassSession.objects.create(
                institution=self.inst_a,
                course_offering=self.offering_a,
                session_date=date(2025, 9, 22),
            )

    def test_participation_score_outside_scale_blocked(self):
        first = ParticipationRecord.objects.create(
            institution=self.inst_a,
            class_session=self.class_session_a,
            student=self.student_b,
            score=3,
        )
        self.assertIsNotNone(first.id)
        with self.assertRaises(IntegrityError):
            ParticipationRecord.objects.create(
                institution=self.inst_a,
                class_session=self.class_session_a,
                student=self.student_b,
                score=9,
            )

    def test_second_current_prediction_blocked(self):
        first = RiskPrediction.objects.create(
            institution=self.inst_a,
            student=self.student_a,
            semester=self.semester_a,
            risk_level=RiskLevel.LOW,
            risk_score=0.1,
        )
        self.assertTrue(first.is_current)
        with self.assertRaises(IntegrityError):
            RiskPrediction.objects.create(
                institution=self.inst_a,
                student=self.student_a,
                semester=self.semester_a,
                risk_level=RiskLevel.MEDIUM,
                risk_score=0.5,
            )

    def test_cross_tenant_offering_rejected_at_save(self):
        self.assertFalse(CourseOffering.objects.filter(institution=self.inst_b).exists())
        with self.assertRaises(ValidationError):
            CourseOffering.objects.create(
                institution=self.inst_b,
                course=self.course_a,
                session=self.session_a,
                semester=self.semester_a,
            )
        self.assertFalse(CourseOffering.objects.filter(institution=self.inst_b).exists())


class HealthEndpointTests(Phase6FixtureMixin, TestCase):
    """The readiness probe answers without authentication or data writes."""

    def test_health_reports_database_ok(self):
        import json

        response = self.client.get(reverse('health'))
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['checks']['database'], 'ok')

    def test_health_is_get_only(self):
        response = self.client.post(reverse('health'))
        self.assertEqual(response.status_code, 405)


class DashboardQueryBudgetTests(Phase6FixtureMixin, TestCase):
    """Per-dashboard query budgets: a canary against N+1 regressions.

    The budgets are the measured query counts of the Phase 6 build on a
    representative dataset (one enrolment, two assessments, one class
    session with attendance and participation, one advisee assignment).
    If a change makes a dashboard slower, this fails before production does.
    """

    def _get_within_budget(self, user, url_name, budget):
        self.client.force_login(user)
        with self.assertNumQueries(budget):
            response = self.client.get(reverse(url_name))
        self.assertEqual(response.status_code, 200)

    def test_student_dashboard(self):
        self._get_within_budget(
            self.student_a, 'dashboard:student', STUDENT_DASHBOARD_QUERIES
        )

    def test_student_progress_dashboard(self):
        self._get_within_budget(
            self.student_a, 'dashboard:student_progress',
            STUDENT_PROGRESS_QUERIES,
        )

    def test_instructor_dashboard(self):
        self._get_within_budget(
            self.instructor_a, 'dashboard:instructor', INSTRUCTOR_DASHBOARD_QUERIES
        )

    def test_instructor_performance_dashboard(self):
        self._get_within_budget(
            self.instructor_a, 'dashboard:instructor_performance',
            INSTRUCTOR_PERFORMANCE_QUERIES,
        )

    def test_adviser_dashboard(self):
        self._get_within_budget(
            self.adviser_a, 'dashboard:adviser', ADVISER_DASHBOARD_QUERIES
        )

    def test_adviser_progress_dashboard(self):
        self._get_within_budget(
            self.adviser_a, 'dashboard:adviser_progress', ADVISER_PROGRESS_QUERIES
        )
