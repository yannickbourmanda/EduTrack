"""
Phase 6 remediation tests - EduTrack.

Focused regression tests for the verified corrections: registration password
policy, inactive-institution access control, machine-learning integrity
(final-exam exclusion and minimum evidence), GET-safe prediction pages,
weighted analytics, current-semester scoping and CSRF enforcement.
"""

from datetime import date

from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Membership, MembershipStatus, Role, User
from academics.models import (
    AcademicSession,
    Course,
    CourseOffering,
    Enrollment,
    Semester,
)
from dashboard import analytics
from institutions.models import Department, Faculty, Institution
from monitoring.models import (
    Assessment,
    AssessmentResult,
    AssessmentType,
)
from predictions.models import RiskPrediction
from predictions.services import RiskPredictionService

PASSWORD = 'Password123!'


class RemediationFixtureMixin:
    """Shared tenants and users for the remediation tests."""

    @classmethod
    def setUpTestData(cls):
        cls.inst_a = Institution.objects.create(
            name='American University of Nigeria',
            code='AUN',
            slug='aun',
            contact_email='admin@aun.edu.ng',
        )
        # A deactivated tenant: its records must not be reachable.
        cls.inst_dead = Institution.objects.create(
            name='Closed University',
            code='CLOSED',
            slug='closed',
            contact_email='admin@closed.edu',
            is_active=False,
        )

        cls.admin_a = cls._user('admin@aun.edu.ng', 'Amina', 'Bello')
        cls.registrar_a = cls._user('registrar@aun.edu.ng', 'Rita', 'Okafor')
        cls.instructor_a = cls._user('instructor@aun.edu.ng', 'Ibrahim', 'Musa')
        cls.student_a = cls._user('student@aun.edu.ng', 'Emmanuel', 'Okafor')
        cls.student_dead = cls._user('student@closed.edu', 'Tunde', 'Ghost')
        cls.student_mixed = cls._user('mixed@aun.edu.ng', 'Chidi', 'Both')

        cls._member(cls.admin_a, cls.inst_a, Role.INSTITUTION_ADMIN)
        cls._member(cls.registrar_a, cls.inst_a, Role.REGISTRAR)
        cls._member(cls.instructor_a, cls.inst_a, Role.INSTRUCTOR)
        cls._member(cls.student_a, cls.inst_a, Role.STUDENT)
        cls._member(cls.student_dead, cls.inst_dead, Role.STUDENT)
        # One user with an approved membership in an active institution AND
        # one in a deactivated institution.
        cls._member(cls.student_mixed, cls.inst_a, Role.STUDENT)
        cls._member(cls.student_mixed, cls.inst_dead, Role.INSTRUCTOR)

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
        cls.past_session = AcademicSession.objects.create(
            institution=cls.inst_a,
            name='2024/2025',
            start_date=date(2024, 9, 1),
            end_date=date(2025, 6, 30),
        )
        cls.semester_a = Semester.objects.create(
            institution=cls.inst_a,
            session=cls.session_a,
            name='First Semester',
            start_date=date(2025, 9, 8),
            end_date=date(2026, 1, 20),
            is_current=True,
        )
        cls.past_semester = Semester.objects.create(
            institution=cls.inst_a,
            session=cls.past_session,
            name='First Semester',
            start_date=date(2024, 9, 8),
            end_date=date(2025, 1, 20),
        )
        cls.course_a = Course.objects.create(
            institution=cls.inst_a,
            department=cls.dept_a,
            code='CSC301',
            title='Object-Oriented Software Construction',
            credit_units=3,
        )
        cls.course_old = Course.objects.create(
            institution=cls.inst_a,
            department=cls.dept_a,
            code='CSC100',
            title='Introduction to Programming',
            credit_units=3,
        )
        cls.offering_a = CourseOffering.objects.create(
            institution=cls.inst_a,
            course=cls.course_a,
            session=cls.session_a,
            semester=cls.semester_a,
            instructor=cls.instructor_a,
        )
        cls.offering_old = CourseOffering.objects.create(
            institution=cls.inst_a,
            course=cls.course_old,
            session=cls.past_session,
            semester=cls.past_semester,
            instructor=cls.instructor_a,
        )
        cls.enrollment_a = Enrollment.objects.create(
            institution=cls.inst_a,
            student=cls.student_a,
            course_offering=cls.offering_a,
        )
        cls.enrollment_old = Enrollment.objects.create(
            institution=cls.inst_a,
            student=cls.student_a,
            course_offering=cls.offering_old,
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

    def _login(self, user):
        self.assertTrue(
            self.client.login(email=user.email, password=PASSWORD),
            f'Could not sign in as {user.email}',
        )


class RegistrationPasswordPolicyTests(RemediationFixtureMixin, TestCase):
    """Registration must enforce Django's password validators."""

    def _register(self, password, email='new.student@aun.edu.ng'):
        return self.client.post(reverse('accounts:register'), {
            'first_name': 'Ngozi',
            'last_name': 'New',
            'email': email,
            'phone_number': '+2348000000000',
            'institution': str(self.inst_a.id),
            'requested_role': Role.STUDENT,
            'id_number': 'A0009999',
            'password': password,
            'confirm_password': password,
        })

    def test_numeric_only_password_rejected(self):
        response = self._register('12345678')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'entirely numeric', status_code=200)
        self.assertFalse(
            User.objects.filter(email='new.student@aun.edu.ng').exists()
        )

    def test_common_password_rejected(self):
        response = self._register('password')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'too common')
        self.assertFalse(
            User.objects.filter(email='new.student@aun.edu.ng').exists()
        )

    def test_short_password_rejected(self):
        response = self._register('Ab1!')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'at least 8 characters')
        self.assertFalse(
            User.objects.filter(email='new.student@aun.edu.ng').exists()
        )

    def test_password_similar_to_name_rejected(self):
        response = self.client.post(reverse('accounts:register'), {
            'first_name': 'Emmanuel',
            'last_name': 'Okafor',
            'email': 'new.student@aun.edu.ng',
            'phone_number': '+2348000000000',
            'institution': str(self.inst_a.id),
            'requested_role': Role.STUDENT,
            'id_number': 'A0009999',
            'password': 'EmmanuelOkafor',
            'confirm_password': 'EmmanuelOkafor',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'too similar')
        self.assertFalse(
            User.objects.filter(email='new.student@aun.edu.ng').exists()
        )

    def test_strong_password_accepted_and_membership_pending(self):
        response = self._register('S7rong!Passphrase')
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse('accounts:pending_approval'), response['Location']
        )
        user = User.objects.get(email='new.student@aun.edu.ng')
        self.assertTrue(
            user.check_password('S7rong!Passphrase')
        )
        membership = Membership.objects.get(user=user)
        self.assertEqual(membership.status, MembershipStatus.PENDING)
        self.assertEqual(membership.role, Role.STUDENT)


class InactiveInstitutionTests(RemediationFixtureMixin, TestCase):
    """A deactivated institution must not serve records to its members."""

    def test_login_of_inactive_only_member_lands_on_unavailable_page(self):
        self._login(self.student_dead)
        response = self.client.get(
            reverse('accounts:pending_approval'), follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'currently unavailable')

    def test_inactive_member_cannot_reach_any_tenant_page(self):
        self._login(self.student_dead)
        urls = [
            reverse('dashboard:home'),
            reverse('dashboard:student'),
            reverse('academics:my_courses'),
            reverse('monitoring:my_grades'),
            reverse('predictions:my_risk'),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url, follow=False)
                self.assertEqual(response.status_code, 302)
                self.assertIn(
                    reverse('accounts:pending_approval'),
                    response['Location'],
                )

    def test_unavailable_page_shows_no_records(self):
        self._login(self.student_dead)
        response = self.client.get(reverse('accounts:pending_approval'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The page must not render course or grade information.
        self.assertNotIn('CSC301', body)

    def test_switching_to_inactive_membership_is_rejected(self):
        dead_membership = Membership.objects.get(
            user=self.student_dead, institution=self.inst_dead
        )
        self._login(self.student_dead)
        response = self.client.post(
            reverse(
                'accounts:switch_institution',
                args=[dead_membership.id],
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_member_with_active_tenant_elsewhere_still_works(self):
        # The mixed user's active membership is at inst_a; the deactivated
        # inst_dead membership must not lock them out of inst_a.
        self._login(self.student_mixed)
        response = self.client.get(reverse('dashboard:student'))
        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertEqual(
            context['active_membership'].institution, self.inst_a
        )

    def test_inactive_institution_hidden_from_switcher(self):
        self._login(self.student_mixed)
        response = self.client.get(reverse('dashboard:student'))
        body = response.content.decode()
        self.assertNotIn('CLOSED', body)

    def test_deactivated_institution_name_does_not_appear_for_dead_member(self):
        # Even the unavailable-notice page only names the institution in the
        # explanatory copy; no academic data is shown.
        self._login(self.student_dead)
        response = self.client.get(reverse('dashboard:home'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'currently unavailable')


class MachineLearningIntegrityTests(RemediationFixtureMixin, TestCase):
    """Final-exam exclusion, evidence requirement and coverage disclosure."""

    def _evidence(self, student, include_final=False):
        quiz = Assessment.objects.create(
            institution=self.inst_a,
            course_offering=self.offering_a,
            title='Quiz 1',
            assessment_type=AssessmentType.QUIZ,
            max_score=20.0,
        )
        AssessmentResult.objects.create(
            institution=self.inst_a,
            assessment=quiz,
            student=student,
            score=18.0,  # 90%
        )
        if include_final:
            final = Assessment.objects.create(
                institution=self.inst_a,
                course_offering=self.offering_a,
                title='Final Examination',
                assessment_type=AssessmentType.FINAL_EXAM,
                max_score=100.0,
            )
            AssessmentResult.objects.create(
                institution=self.inst_a,
                assessment=final,
                student=student,
                score=0.0,  # would drag the average if leaked
            )
        return quiz

    def test_extract_features_excludes_final_exam(self):
        self._evidence(self.student_a, include_final=True)
        features = RiskPredictionService.extract_student_features(
            self.student_a, self.semester_a, self.inst_a
        )
        self.assertEqual(features['assessment_avg'], 90.0)
        self.assertEqual(features['quiz_avg'], 90.0)
        self.assertEqual(features['failed_assessments_count'], 0)
        self.assertEqual(features['data_coverage']['assessment_results'], 1)

    def test_prediction_refused_without_recorded_evidence(self):
        # Enrollment exists but nothing has been graded or recorded.
        prediction = RiskPredictionService.evaluate_and_predict(
            self.student_a, self.semester_a, self.inst_a
        )
        self.assertIsNone(prediction)
        self.assertEqual(RiskPrediction.objects.count(), 0)

    def test_snapshot_discloses_data_coverage(self):
        self._evidence(self.student_a)
        prediction = RiskPredictionService.evaluate_and_predict(
            self.student_a, self.semester_a, self.inst_a
        )
        self.assertIsNotNone(prediction)
        coverage = prediction.feature_snapshot['data_coverage']
        self.assertEqual(coverage['assessment_results'], 1)
        self.assertEqual(coverage['active_enrollments'], 1)
        self.assertEqual(coverage['attendance_records'], 0)
        self.assertIn('assessment_avg', prediction.feature_snapshot)

    def test_refresh_replaces_previous_estimate_atomically(self):
        self._evidence(self.student_a)
        first = RiskPredictionService.evaluate_and_predict(
            self.student_a, self.semester_a, self.inst_a
        )
        second = RiskPredictionService.evaluate_and_predict(
            self.student_a, self.semester_a, self.inst_a
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        first.refresh_from_db()
        self.assertFalse(first.is_current)
        self.assertTrue(second.is_current)
        self.assertEqual(
            RiskPrediction.objects.filter(
                student=self.student_a, is_current=True
            ).count(),
            1,
        )


class NoPredictionWritesOnGetTests(RemediationFixtureMixin, TestCase):
    """Viewing a page must never generate or replace an estimate."""

    def test_student_dashboard_get_creates_no_prediction(self):
        self._evidence_setup()
        self._login(self.student_a)
        response = self.client.get(reverse('dashboard:student'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RiskPrediction.objects.count(), 0)
        self.assertIsNone(response.context['risk_prediction'])

    def test_my_risk_get_creates_no_prediction(self):
        self._evidence_setup()
        self._login(self.student_a)
        response = self.client.get(reverse('predictions:my_risk'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RiskPrediction.objects.count(), 0)
        self.assertIsNone(response.context['prediction'])

    def _evidence_setup(self):
        """Recorded coursework, so the pre-fix code would have predicted."""
        quiz = Assessment.objects.create(
            institution=self.inst_a,
            course_offering=self.offering_a,
            title='Quiz 1',
            assessment_type=AssessmentType.QUIZ,
            max_score=20.0,
        )
        AssessmentResult.objects.create(
            institution=self.inst_a,
            assessment=quiz,
            student=self.student_a,
            score=10.0,
        )


class WeightedAnalyticsTests(RemediationFixtureMixin, TestCase):
    """Assessment averages follow each assessment's official weight."""

    def test_weighted_average_across_assessments(self):
        midterm = Assessment.objects.create(
            institution=self.inst_a,
            course_offering=self.offering_a,
            title='Midterm',
            assessment_type=AssessmentType.MIDTERM,
            max_score=100.0,
            weight_percentage=70.0,
        )
        quiz = Assessment.objects.create(
            institution=self.inst_a,
            course_offering=self.offering_a,
            title='Quiz 1',
            assessment_type=AssessmentType.QUIZ,
            max_score=100.0,
            weight_percentage=30.0,
        )
        AssessmentResult.objects.create(
            institution=self.inst_a, assessment=midterm,
            student=self.student_a, score=100.0,
        )
        AssessmentResult.objects.create(
            institution=self.inst_a, assessment=quiz,
            student=self.student_a, score=50.0,
        )

        performance_map = analytics.assessment_performance_map(
            self.inst_a, [self.student_a.id]
        )
        key = (self.offering_a.id, self.student_a.id)
        self.assertEqual(performance_map[key], 85.0)

    def test_zero_weight_results_fall_back_to_unweighted_mean(self):
        first = Assessment.objects.create(
            institution=self.inst_a,
            course_offering=self.offering_a,
            title='Unweighted A',
            max_score=100.0,
            weight_percentage=0.0,
        )
        second = Assessment.objects.create(
            institution=self.inst_a,
            course_offering=self.offering_a,
            title='Unweighted B',
            max_score=100.0,
            weight_percentage=0.0,
        )
        AssessmentResult.objects.create(
            institution=self.inst_a, assessment=first,
            student=self.student_a, score=60.0,
        )
        AssessmentResult.objects.create(
            institution=self.inst_a, assessment=second,
            student=self.student_a, score=100.0,
        )
        performance_map = analytics.assessment_performance_map(
            self.inst_a, [self.student_a.id]
        )
        self.assertEqual(
            performance_map[(self.offering_a.id, self.student_a.id)], 80.0
        )


class CurrentSemesterScopingTests(RemediationFixtureMixin, TestCase):
    """The student dashboard reports the current semester, not all history."""

    def test_dashboard_counts_only_current_semester_courses(self):
        self._login(self.student_a)
        response = self.client.get(reverse('dashboard:student'))
        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertEqual(context['registered_courses'], 1)
        self.assertEqual(
            [row['course'].code for row in context['course_rows']],
            ['CSC301'],
        )
        self.assertEqual(context['current_semester'], self.semester_a)

    def test_progress_page_still_covers_all_semesters(self):
        self._login(self.student_a)
        response = self.client.get(reverse('dashboard:student_progress'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['progress']['course_count'], 2)


class RegistrarStudentCountsTests(RemediationFixtureMixin, TestCase):
    """Enrolment counts on the registrar list come from one annotated query."""

    def test_registrar_students_view_reports_enrolment_counts(self):
        self._login(self.registrar_a)
        response = self.client.get(reverse('dashboard:registrar_students'))
        self.assertEqual(response.status_code, 200)
        rows = {
            row['membership'].user_id: row['enrol_count']
            for row in response.context['student_rows']
        }
        self.assertEqual(rows[self.student_a.id], 2)
        self.assertEqual(rows[self.student_mixed.id], 0)


class CSRFEnforcementTests(RemediationFixtureMixin, TestCase):
    """State-changing POSTs require a CSRF token even when authenticated."""

    def test_membership_approval_rejects_post_without_csrf_token(self):
        pending = self._member(
            self._user('applicant@aun.edu.ng', 'Ada', 'Applicant'),
            self.inst_a,
            Role.STUDENT,
            MembershipStatus.PENDING,
        )
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertTrue(
            csrf_client.login(email=self.admin_a.email, password=PASSWORD)
        )
        response = csrf_client.post(
            reverse('accounts:approve_membership', args=[pending.id])
        )
        self.assertEqual(response.status_code, 403)
        pending.refresh_from_db()
        self.assertEqual(pending.status, MembershipStatus.PENDING)


class DuplicateSubmissionTests(RemediationFixtureMixin, TestCase):
    """Database constraints block duplicate academic records."""

    def test_duplicate_enrollment_blocked_by_unique_constraint(self):
        with self.assertRaises(IntegrityError):
            Enrollment.objects.create(
                institution=self.inst_a,
                student=self.student_a,
                course_offering=self.offering_a,
            )
