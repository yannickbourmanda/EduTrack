"""
Access-control and tenant-isolation tests for EduTrack.

These tests exercise the real HTTP layer with the Django test client: every
scenario signs in as a real user, calls the real view and asserts the real
status code / rendered data. They cover role separation, object-level access
(IDOR), cross-institution isolation and POST-only state changes.
"""

from datetime import date
from decimal import Decimal

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
from advising.models import (
    AcademicIntervention,
    AdviserAssignment,
    InterventionStatus,
    InterventionType,
)
from institutions.models import Department, Faculty, Institution
from monitoring.models import (
    Assessment,
    AssessmentResult,
    AttendanceRecord,
    AttendanceStatus,
    ClassSession,
)
from predictions.models import RiskLevel, RiskPrediction

PASSWORD = 'Password123!'


class RoleAccessControlTests(TestCase):
    """Role dashboards, object-level authorisation and tenant isolation."""

    @classmethod
    def setUpTestData(cls):
        cls.inst_a = Institution.objects.create(
            name='American University of Nigeria',
            code='AUN',
            slug='aun',
            contact_email='admin@aun.edu.ng',
        )
        cls.inst_b = Institution.objects.create(
            name='University of Lagos',
            code='UNILAG',
            slug='unilag',
            contact_email='admin@unilag.edu.ng',
        )

        cls.platform_admin = cls._user(
            'platform.admin@edutrack.edu', 'Platform', 'Admin', superuser=True
        )
        cls.admin_a = cls._user('admin@aun.edu.ng', 'Amina', 'Bello')
        cls.registrar_a = cls._user('registrar@aun.edu.ng', 'Rita', 'Okafor')
        cls.instructor_a = cls._user('instructor.a@aun.edu.ng', 'Ibrahim', 'Musa')
        cls.instructor_a2 = cls._user('instructor.a2@aun.edu.ng', 'Grace', 'Eze')
        cls.adviser_a = cls._user('adviser.a@aun.edu.ng', 'Chioma', 'Nwosu')
        cls.adviser_b = cls._user('adviser.b@aun.edu.ng', 'Tunde', 'Ade')
        cls.student_a1 = cls._user('student.a1@aun.edu.ng', 'Emmanuel', 'Okafor')
        cls.student_a2 = cls._user('student.a2@aun.edu.ng', 'Zainab', 'Bello')
        cls.admin_b = cls._user('admin@unilag.edu.ng', 'Folake', 'Ade')
        cls.instructor_b = cls._user('instructor.b@unilag.edu.ng', 'Segun', 'Akin')
        cls.student_b = cls._user('student.b@unilag.edu.ng', 'Kunle', 'Balogun')
        cls.pending_a = cls._user('pending.a@aun.edu.ng', 'Peter', 'Pending')
        cls.pending_b = cls._user('pending.b@unilag.edu.ng', 'Sarah', 'Waiting')

        cls._member(cls.admin_a, cls.inst_a, Role.INSTITUTION_ADMIN)
        cls._member(cls.registrar_a, cls.inst_a, Role.REGISTRAR)
        cls._member(cls.instructor_a, cls.inst_a, Role.INSTRUCTOR)
        cls._member(cls.instructor_a2, cls.inst_a, Role.INSTRUCTOR)
        cls._member(cls.adviser_a, cls.inst_a, Role.ADVISER)
        cls._member(cls.adviser_b, cls.inst_a, Role.ADVISER)
        cls._member(cls.student_a1, cls.inst_a, Role.STUDENT)
        cls._member(cls.student_a2, cls.inst_a, Role.STUDENT)
        cls._member(cls.admin_b, cls.inst_b, Role.INSTITUTION_ADMIN)
        cls._member(cls.instructor_b, cls.inst_b, Role.INSTRUCTOR)
        cls._member(cls.student_b, cls.inst_b, Role.STUDENT)
        cls._member(
            cls.pending_a, cls.inst_a, Role.STUDENT, MembershipStatus.PENDING
        )
        cls._member(
            cls.pending_b, cls.inst_b, Role.STUDENT, MembershipStatus.PENDING
        )

        # --- Institution A academic structure -------------------------------
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
        cls.course_a1 = Course.objects.create(
            institution=cls.inst_a,
            department=cls.dept_a,
            code='CSC301',
            title='Object-Oriented Software Construction',
            credit_units=3,
        )
        cls.course_a2 = Course.objects.create(
            institution=cls.inst_a,
            department=cls.dept_a,
            code='CSC302',
            title='Database Systems',
            credit_units=3,
        )
        cls.offering_a1 = CourseOffering.objects.create(
            institution=cls.inst_a,
            course=cls.course_a1,
            session=cls.session_a,
            semester=cls.semester_a,
            instructor=cls.instructor_a,
        )
        cls.offering_a2 = CourseOffering.objects.create(
            institution=cls.inst_a,
            course=cls.course_a2,
            session=cls.session_a,
            semester=cls.semester_a,
            instructor=cls.instructor_a2,
        )
        cls.enrollment_a1 = Enrollment.objects.create(
            institution=cls.inst_a,
            student=cls.student_a1,
            course_offering=cls.offering_a1,
        )
        cls.enrollment_a2 = Enrollment.objects.create(
            institution=cls.inst_a,
            student=cls.student_a2,
            course_offering=cls.offering_a2,
        )

        # --- Institution B academic structure -------------------------------
        cls.faculty_b = Faculty.objects.create(
            institution=cls.inst_b, name='Science', code='SCI'
        )
        cls.dept_b = Department.objects.create(
            institution=cls.inst_b,
            faculty=cls.faculty_b,
            name='Computer Science',
            code='CS',
        )
        cls.session_b = AcademicSession.objects.create(
            institution=cls.inst_b,
            name='2025/2026',
            start_date=date(2025, 9, 1),
            end_date=date(2026, 6, 30),
            is_current=True,
        )
        cls.semester_b = Semester.objects.create(
            institution=cls.inst_b,
            session=cls.session_b,
            name='First Semester',
            start_date=date(2025, 9, 8),
            end_date=date(2026, 1, 20),
            is_current=True,
        )
        cls.course_b1 = Course.objects.create(
            institution=cls.inst_b,
            department=cls.dept_b,
            code='UNI101',
            title='Unilag Introduction to Computing',
            credit_units=3,
        )
        cls.offering_b1 = CourseOffering.objects.create(
            institution=cls.inst_b,
            course=cls.course_b1,
            session=cls.session_b,
            semester=cls.semester_b,
            instructor=cls.instructor_b,
        )
        cls.enrollment_b1 = Enrollment.objects.create(
            institution=cls.inst_b,
            student=cls.student_b,
            course_offering=cls.offering_b1,
        )

        # --- Assessments and results ----------------------------------------
        cls.assessment_a1 = Assessment.objects.create(
            institution=cls.inst_a,
            course_offering=cls.offering_a1,
            title='Midterm A',
            max_score=Decimal('50'),
        )
        cls.assessment_a2 = Assessment.objects.create(
            institution=cls.inst_a,
            course_offering=cls.offering_a2,
            title='Quiz B',
            max_score=Decimal('50'),
        )
        cls.assessment_b1 = Assessment.objects.create(
            institution=cls.inst_b,
            course_offering=cls.offering_b1,
            title='Midterm B',
            max_score=Decimal('50'),
        )
        cls.result_a1 = AssessmentResult.objects.create(
            institution=cls.inst_a,
            assessment=cls.assessment_a1,
            student=cls.student_a1,
            score=Decimal('40'),
            feedback='AUN-OWN-FEEDBACK',
        )
        cls.result_a2 = AssessmentResult.objects.create(
            institution=cls.inst_a,
            assessment=cls.assessment_a2,
            student=cls.student_a2,
            score=Decimal('30'),
            feedback='OTHER-STUDENT-FEEDBACK',
        )
        cls.result_b1 = AssessmentResult.objects.create(
            institution=cls.inst_b,
            assessment=cls.assessment_b1,
            student=cls.student_b,
            score=Decimal('45'),
            feedback='UNILAG-FEEDBACK',
        )

        # --- Attendance ------------------------------------------------------
        cls.class_session_a1 = ClassSession.objects.create(
            institution=cls.inst_a,
            course_offering=cls.offering_a1,
            session_date=date(2025, 9, 10),
            topic='Own lecture',
        )
        cls.class_session_a2 = ClassSession.objects.create(
            institution=cls.inst_a,
            course_offering=cls.offering_a2,
            session_date=date(2025, 9, 11),
            topic='Other lecture',
        )
        AttendanceRecord.objects.create(
            institution=cls.inst_a,
            class_session=cls.class_session_a1,
            student=cls.student_a1,
            status=AttendanceStatus.PRESENT,
            notes='OWN-ATTENDANCE-NOTE',
        )
        AttendanceRecord.objects.create(
            institution=cls.inst_a,
            class_session=cls.class_session_a2,
            student=cls.student_a2,
            status=AttendanceStatus.ABSENT,
            notes='OTHER-ATTENDANCE-NOTE',
        )

        # --- Advising --------------------------------------------------------
        AdviserAssignment.objects.create(
            institution=cls.inst_a, adviser=cls.adviser_a, student=cls.student_a1
        )
        AdviserAssignment.objects.create(
            institution=cls.inst_a, adviser=cls.adviser_b, student=cls.student_a2
        )
        cls.intervention_b = AcademicIntervention.objects.create(
            institution=cls.inst_a,
            student=cls.student_a2,
            adviser=cls.adviser_b,
            intervention_type=InterventionType.COUNSELLING,
            status=InterventionStatus.IN_PROGRESS,
            reason='OTHER-INTERVENTION-REASON',
            action_taken='Private action plan',
            date_conducted=date(2025, 10, 1),
        )

        # --- Predictions -----------------------------------------------------
        RiskPrediction.objects.create(
            institution=cls.inst_a,
            student=cls.student_a1,
            semester=cls.semester_a,
            risk_level=RiskLevel.LOW,
            risk_score=0.1,
        )
        RiskPrediction.objects.create(
            institution=cls.inst_a,
            student=cls.student_a2,
            semester=cls.semester_a,
            risk_level=RiskLevel.HIGH,
            risk_score=0.9,
        )
        RiskPrediction.objects.create(
            institution=cls.inst_b,
            student=cls.student_b,
            semester=cls.semester_b,
            risk_level=RiskLevel.MEDIUM,
            risk_score=0.5,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _user(email, first_name, last_name, superuser=False):
        if superuser:
            return User.objects.create_superuser(
                username=email,
                email=email,
                password=PASSWORD,
                first_name=first_name,
                last_name=last_name,
            )
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

    def _get(self, user, url):
        self._login(user)
        return self.client.get(url)

    # ------------------------------------------------------------------
    # Authentication basics
    # ------------------------------------------------------------------

    def test_login_page_does_not_expose_credentials_or_demo_shortcuts(self):
        response = self.client.get(reverse('accounts:login'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn(PASSWORD, body)
        self.assertNotIn('demo', body.lower())

    def test_protected_pages_redirect_anonymous_users_to_login(self):
        protected = [
            reverse('dashboard:home'),
            reverse('dashboard:student'),
            reverse('dashboard:registrar'),
            reverse('academics:course_list'),
            reverse('monitoring:my_grades'),
            reverse('advising:assigned_students'),
            reverse('predictions:risk_overview'),
            reverse('institutions:structure_view'),
        ]
        for url in protected:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('accounts:login'), response['Location'])

    def test_logout_requires_post(self):
        self._login(self.student_a1)
        self.assertEqual(
            self.client.get(reverse('accounts:logout')).status_code, 405
        )

    # ------------------------------------------------------------------
    # Dashboard routing
    # ------------------------------------------------------------------

    def test_home_redirects_each_role_to_its_own_dashboard(self):
        cases = [
            (self.platform_admin, 'dashboard:platform_admin'),
            (self.student_a1, 'dashboard:student'),
            (self.instructor_a, 'dashboard:instructor'),
            (self.adviser_a, 'dashboard:adviser'),
            (self.registrar_a, 'dashboard:registrar'),
            (self.admin_a, 'dashboard:institution_admin'),
        ]
        for user, target in cases:
            with self.subTest(user=user.email):
                self._login(user)
                response = self.client.get(reverse('dashboard:home'))
                self.assertRedirects(
                    response, reverse(target), fetch_redirect_response=False
                )
                self.client.logout()

    def test_pending_membership_is_sent_to_pending_approval(self):
        self._login(self.pending_a)
        response = self.client.get(reverse('dashboard:student'), follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:pending_approval'), response['Location'])

    # ------------------------------------------------------------------
    # Student boundary
    # ------------------------------------------------------------------

    def test_student_can_open_own_pages(self):
        self._login(self.student_a1)
        urls = [
            reverse('dashboard:student'),
            reverse('dashboard:student_progress'),
            reverse('academics:my_courses'),
            reverse('monitoring:my_grades'),
            reverse('monitoring:my_attendance'),
            reverse('predictions:my_risk'),
            reverse('accounts:profile'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_student_cannot_open_staff_or_platform_pages(self):
        self._login(self.student_a1)
        urls = [
            reverse('dashboard:instructor'),
            reverse('dashboard:instructor_students'),
            reverse('dashboard:instructor_performance'),
            reverse('dashboard:adviser'),
            reverse('dashboard:adviser_progress'),
            reverse('dashboard:registrar'),
            reverse('dashboard:registrar_students'),
            reverse('dashboard:institution_admin'),
            reverse('dashboard:reports'),
            reverse('dashboard:audit_logs'),
            reverse('dashboard:platform_admin'),
            reverse('dashboard:platform_users'),
            reverse('dashboard:platform_activity'),
            reverse('academics:course_list'),
            reverse('academics:offering_list'),
            reverse('academics:enrollment_list'),
            reverse('academics:session_list'),
            reverse('academics:registrar_results'),
            reverse('monitoring:assessment_management'),
            reverse('monitoring:attendance_overview'),
            reverse('advising:assigned_students'),
            reverse('advising:intervention_list'),
            reverse('predictions:risk_overview'),
            reverse('predictions:model_metrics'),
            reverse('accounts:membership_management'),
            reverse('institutions:structure_view'),
            reverse('institutions:institution_list'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_student_only_sees_own_results_and_attendance(self):
        self._login(self.student_a1)

        grades = self.client.get(reverse('monitoring:my_grades'))
        self.assertContains(grades, 'AUN-OWN-FEEDBACK')
        self.assertNotContains(grades, 'OTHER-STUDENT-FEEDBACK')
        self.assertNotContains(grades, 'UNILAG-FEEDBACK')

        attendance = self.client.get(reverse('monitoring:my_attendance'))
        self.assertContains(attendance, 'OWN-ATTENDANCE-NOTE')
        self.assertNotContains(attendance, 'OTHER-ATTENDANCE-NOTE')

    def test_student_dashboard_excludes_other_students_and_other_institutions(self):
        self._login(self.student_a1)
        response = self.client.get(reverse('dashboard:student'))
        self.assertNotContains(response, 'OTHER-INTERVENTION-REASON')
        self.assertNotContains(response, 'UNILAG-FEEDBACK')

        courses = self.client.get(reverse('academics:my_courses'))
        self.assertContains(courses, 'CSC301')
        self.assertNotContains(courses, 'UNI101')

    # ------------------------------------------------------------------
    # Instructor / lecturer boundary
    # ------------------------------------------------------------------

    def test_instructor_can_open_own_teaching_pages(self):
        self._login(self.instructor_a)
        urls = [
            reverse('dashboard:instructor'),
            reverse('dashboard:instructor_performance'),
            reverse('dashboard:instructor_students'),
            reverse('academics:instructor_courses'),
            reverse('monitoring:assessment_management'),
            reverse('monitoring:attendance_overview'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_instructor_only_sees_assigned_courses_and_students(self):
        self._login(self.instructor_a)
        performance = self.client.get(reverse('dashboard:instructor_performance'))
        self.assertContains(performance, 'CSC301')
        self.assertNotContains(performance, 'CSC302')

        students = self.client.get(reverse('dashboard:instructor_students'))
        self.assertContains(students, 'Emmanuel')
        self.assertNotContains(students, 'Zainab')

    def test_instructor_cannot_open_another_instructors_course(self):
        self._login(self.instructor_a)
        urls = [
            reverse(
                'monitoring:grade_assessment',
                args=[self.assessment_a2.id],
            ),
            reverse('monitoring:enter_scores', args=[self.offering_a2.id]),
            reverse('monitoring:record_attendance', args=[self.offering_a2.id]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_instructor_cannot_post_scores_to_unassigned_course(self):
        self._login(self.instructor_a)
        response = self.client.post(
            reverse('monitoring:grade_assessment', args=[self.assessment_a2.id]),
            {f'score_{self.student_a2.id}': '40'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            AssessmentResult.objects.filter(
                assessment=self.assessment_a2,
                student=self.student_a2,
                score=Decimal('40'),
            ).exists()
        )

    def test_instructor_cannot_reach_other_institution_objects(self):
        self._login(self.instructor_a)
        self.assertEqual(
            self.client.get(
                reverse('monitoring:enter_scores', args=[self.offering_b1.id])
            ).status_code,
            404,
        )

    def test_instructor_cannot_open_student_registrar_or_platform_pages(self):
        self._login(self.instructor_a)
        urls = [
            reverse('dashboard:student'),
            reverse('dashboard:registrar'),
            reverse('dashboard:institution_admin'),
            reverse('dashboard:platform_admin'),
            reverse('academics:course_list'),
            reverse('academics:enrollment_list'),
            reverse('accounts:membership_management'),
            reverse('predictions:my_risk'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    # ------------------------------------------------------------------
    # Adviser boundary
    # ------------------------------------------------------------------

    def test_adviser_sees_only_assigned_students(self):
        self._login(self.adviser_a)

        assigned = self.client.get(reverse('advising:assigned_students'))
        self.assertContains(assigned, 'Emmanuel')
        self.assertNotContains(assigned, 'Zainab')

        overview = self.client.get(reverse('predictions:risk_overview'))
        self.assertContains(overview, 'Emmanuel')
        self.assertNotContains(overview, 'Zainab')

    def test_adviser_cannot_access_unassigned_student_records(self):
        self._login(self.adviser_a)

        response = self.client.get(
            reverse('advising:create_intervention_for_student',
                    args=[self.student_a2.id])
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            reverse('advising:intervention_detail',
                    args=[self.intervention_b.id])
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.post(
            reverse('predictions:evaluate_student', args=[self.student_a2.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_adviser_cannot_create_intervention_for_unassigned_student(self):
        self._login(self.adviser_a)
        before = AcademicIntervention.objects.count()
        response = self.client.post(
            reverse('advising:create_intervention'),
            {
                'student': str(self.student_a2.id),
                'intervention_type': InterventionType.COUNSELLING,
                'reason': 'Should be rejected',
                'action_taken': 'Should not be saved',
                'date_conducted': date.today().isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AcademicIntervention.objects.count(), before)

    def test_adviser_cannot_open_other_role_pages(self):
        self._login(self.adviser_a)
        urls = [
            reverse('dashboard:student'),
            reverse('dashboard:instructor'),
            reverse('dashboard:registrar'),
            reverse('dashboard:institution_admin'),
            reverse('dashboard:platform_admin'),
            reverse('monitoring:assessment_management'),
            reverse('academics:enrollment_list'),
            reverse('accounts:membership_management'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    # ------------------------------------------------------------------
    # Registrar and institution administrator boundary
    # ------------------------------------------------------------------

    def test_registrar_can_open_academic_administration_pages(self):
        self._login(self.registrar_a)
        urls = [
            reverse('dashboard:registrar'),
            reverse('dashboard:registrar_students'),
            reverse('academics:course_list'),
            reverse('academics:offering_list'),
            reverse('academics:enrollment_list'),
            reverse('academics:session_list'),
            reverse('academics:registrar_results'),
            reverse('institutions:structure_view'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_registrar_cannot_manage_memberships_or_platform(self):
        self._login(self.registrar_a)
        urls = [
            reverse('accounts:membership_management'),
            reverse('dashboard:institution_admin'),
            reverse('dashboard:reports'),
            reverse('dashboard:audit_logs'),
            reverse('dashboard:platform_admin'),
            reverse('institutions:institution_list'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_registrar_and_admin_only_see_their_institution_results(self):
        for user in (self.registrar_a, self.admin_a):
            with self.subTest(user=user.email):
                self._login(user)
                response = self.client.get(reverse('academics:registrar_results'))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'CSC301')
                self.assertNotContains(response, 'UNI101')
                self.client.logout()

    def test_institution_admin_can_open_own_management_pages(self):
        self._login(self.admin_a)
        urls = [
            reverse('dashboard:institution_admin'),
            reverse('dashboard:reports'),
            reverse('dashboard:audit_logs'),
            reverse('dashboard:registrar_students'),
            reverse('accounts:membership_management'),
            reverse('institutions:structure_view'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_institution_admin_cannot_open_platform_pages(self):
        self._login(self.admin_a)
        urls = [
            reverse('dashboard:platform_admin'),
            reverse('dashboard:platform_users'),
            reverse('dashboard:platform_activity'),
            reverse('institutions:institution_list'),
            reverse('institutions:institution_requests'),
            reverse('institutions:institution_create'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_institution_admin_cannot_grade_or_view_other_institution_records(self):
        self._login(self.admin_a)
        self.assertEqual(
            self.client.get(
                reverse('monitoring:grade_assessment',
                        args=[self.assessment_b1.id])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse('monitoring:enter_scores', args=[self.offering_b1.id])
            ).status_code,
            404,
        )
        reports = self.client.get(reverse('dashboard:reports'))
        self.assertNotContains(reports, 'UNI101')

    def test_institution_admin_membership_review_is_scoped_and_post_only(self):
        pending_a_membership = Membership.objects.get(
            user=self.pending_a, institution=self.inst_a
        )
        pending_b_membership = Membership.objects.get(
            user=self.pending_b, institution=self.inst_b
        )
        self._login(self.admin_a)

        listing = self.client.get(reverse('accounts:membership_management'))
        self.assertContains(listing, self.pending_a.email)
        self.assertNotContains(listing, self.pending_b.email)

        # GET must not mutate state.
        response = self.client.get(
            reverse(
                'accounts:approve_membership',
                args=[pending_a_membership.id],
            )
        )
        self.assertEqual(response.status_code, 405)

        # Cross-institution request must not be reachable at all.
        response = self.client.post(
            reverse(
                'accounts:approve_membership',
                args=[pending_b_membership.id],
            )
        )
        self.assertEqual(response.status_code, 404)

        # Own-institution approval succeeds.
        response = self.client.post(
            reverse(
                'accounts:approve_membership',
                args=[pending_a_membership.id],
            )
        )
        self.assertRedirects(
            response, reverse('accounts:membership_management'),
            fetch_redirect_response=False,
        )
        pending_a_membership.refresh_from_db()
        self.assertEqual(pending_a_membership.status, MembershipStatus.APPROVED)

    def test_assessment_creation_rejects_duplicate_titles(self):
        self._login(self.instructor_a)
        payload = {
            'course_offering': str(self.offering_a1.id),
            'title': 'Duplicate Assessment',
            'assessment_type': 'QUIZ',
            'max_score': '20',
            'weight_percentage': '10',
        }
        first = self.client.post(
            reverse('monitoring:create_assessment'), payload
        )
        self.assertEqual(first.status_code, 302)
        second = self.client.post(
            reverse('monitoring:create_assessment'), payload
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            Assessment.objects.filter(
                institution=self.inst_a,
                course_offering=self.offering_a1,
                title='Duplicate Assessment',
            ).count(),
            1,
        )

    def test_course_creation_rejects_duplicate_codes(self):
        self._login(self.admin_a)
        payload = {
            'department': str(self.dept_a.id),
            'code': 'DUP101',
            'title': 'Duplicate Protection Course',
            'credit_units': '3',
        }
        first = self.client.post(reverse('academics:course_create'), payload)
        self.assertRedirects(
            first, reverse('academics:course_list'),
            fetch_redirect_response=False,
        )
        second = self.client.post(reverse('academics:course_create'), payload)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            Course.objects.filter(
                institution=self.inst_a, code='DUP101'
            ).count(),
            1,
        )

    def test_switch_institution_only_accepts_own_memberships(self):
        self._login(self.student_a1)
        other_membership = Membership.objects.get(
            user=self.student_a2, institution=self.inst_a
        )
        response = self.client.post(
            reverse('accounts:switch_institution', args=[other_membership.id])
        )
        self.assertEqual(response.status_code, 404)

        own_membership = Membership.objects.get(
            user=self.student_a1, institution=self.inst_a
        )
        response = self.client.post(
            reverse('accounts:switch_institution', args=[own_membership.id])
        )
        self.assertRedirects(
            response, reverse('dashboard:home'), fetch_redirect_response=False
        )

    # ------------------------------------------------------------------
    # Platform administrator boundary
    # ------------------------------------------------------------------

    def test_platform_admin_can_open_platform_pages(self):
        self._login(self.platform_admin)
        urls = [
            reverse('dashboard:platform_admin'),
            reverse('dashboard:platform_users'),
            reverse('dashboard:platform_activity'),
            reverse('institutions:institution_list'),
            reverse('institutions:institution_requests'),
            reverse('institutions:institution_create'),
            reverse('predictions:model_metrics'),
            reverse('accounts:profile'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_platform_admin_cannot_read_institution_academic_records(self):
        self._login(self.platform_admin)
        urls = [
            reverse('dashboard:student'),
            reverse('dashboard:instructor'),
            reverse('dashboard:adviser'),
            reverse('dashboard:registrar'),
            reverse('dashboard:registrar_students'),
            reverse('dashboard:institution_admin'),
            reverse('dashboard:reports'),
            reverse('dashboard:audit_logs'),
            reverse('academics:course_list'),
            reverse('academics:offering_list'),
            reverse('academics:enrollment_list'),
            reverse('academics:registrar_results'),
            reverse('monitoring:my_grades'),
            reverse('advising:assigned_students'),
            reverse('predictions:risk_overview'),
            reverse('accounts:membership_management'),
            reverse('institutions:structure_view'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_platform_admin_cannot_grade_through_institution_views(self):
        self._login(self.platform_admin)
        self.assertEqual(
            self.client.get(
                reverse('monitoring:grade_assessment',
                        args=[self.assessment_a1.id])
            ).status_code,
            403,
        )

    # ------------------------------------------------------------------
    # HTTP semantics, search and pagination
    # ------------------------------------------------------------------

    def test_forbidden_and_missing_pages_render_helpful_pages(self):
        self._login(self.student_a1)
        forbidden = self.client.get(reverse('dashboard:instructor'))
        self.assertEqual(forbidden.status_code, 403)
        self.assertContains(forbidden, 'Access denied', status_code=403)

        missing = self.client.get('/this-page-does-not-exist/')
        self.assertEqual(missing.status_code, 404)
        self.assertContains(missing, 'Not found', status_code=404)

    def test_search_filters_are_institution_scoped(self):
        self._login(self.registrar_a)
        response = self.client.get(
            reverse('academics:course_list'), {'q': 'CSC301'}
        )
        self.assertContains(response, 'CSC301')
        self.assertNotContains(response, 'CSC302')

        response = self.client.get(
            reverse('dashboard:registrar_students'), {'q': 'Emmanuel'}
        )
        self.assertContains(response, 'Emmanuel')
        self.assertNotContains(response, 'Zainab')

    def test_pagination_limits_result_sets(self):
        # Add enough students to require a second page of 25.
        for index in range(25):
            user = self._user(
                f'paged.student{index}@aun.edu.ng', f'Paged{index}', 'Student'
            )
            self._member(user, self.inst_a, Role.STUDENT)

        self._login(self.admin_a)
        first_page = self.client.get(reverse('dashboard:registrar_students'))
        second_page = self.client.get(
            reverse('dashboard:registrar_students'), {'page': 2}
        )
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(len(first_page.context['page_obj'].object_list), 25)
        self.assertTrue(second_page.context['page_obj'].has_previous())

    def test_state_changing_prediction_refresh_requires_post(self):
        self._login(self.adviser_a)
        response = self.client.get(
            reverse('predictions:evaluate_student', args=[self.student_a1.id])
        )
        self.assertEqual(response.status_code, 405)
