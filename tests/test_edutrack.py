"""
EduTrack Comprehensive Test Suite
Tests for OOSC domain models, multi-institution tenant isolation,
permission checks, score validations, and machine learning risk inference.
"""

from django.test import TestCase, Client
from django.core.exceptions import ValidationError
from datetime import date
from accounts.models import User, Membership, Role, MembershipStatus
from institutions.models import Institution, Faculty, Department, Programme
from academics.models import AcademicSession, Semester, Course, CourseOffering, Enrollment
from monitoring.models import Assessment, AssessmentType, AssessmentResult, ClassSession, AttendanceRecord, AttendanceStatus
from advising.models import AdviserAssignment, AcademicIntervention, InterventionType, InterventionStatus
from predictions.services import RiskPredictionService


class EduTrackOOSCModelTests(TestCase):
    """Tests testing Object-Oriented encapsulation, constraints, and methods."""

    def setUp(self):
        # Create Test Institution A
        self.inst_a = Institution.objects.create(
            name='American University of Nigeria',
            code='AUN',
            slug='aun',
            contact_email='admin@aun.edu.ng'
        )

        # Create Test Institution B
        self.inst_b = Institution.objects.create(
            name='University of Lagos',
            code='UNILAG',
            slug='unilag',
            contact_email='admin@unilag.edu.ng'
        )

        # Create User
        self.student = User.objects.create_user(
            username='student@aun.edu.ng',
            email='student@aun.edu.ng',
            password='TestPassword123!',
            first_name='Kalu',
            last_name='Uche'
        )

        # Membership in AUN
        self.membership_a = Membership.objects.create(
            user=self.student,
            institution=self.inst_a,
            role=Role.STUDENT,
            status=MembershipStatus.APPROVED
        )

        # Academic Session & Semester
        self.session = AcademicSession.objects.create(
            institution=self.inst_a,
            name='2025/2026',
            start_date=date(2025, 9, 1),
            end_date=date(2026, 6, 30),
            is_current=True
        )
        self.semester = Semester.objects.create(
            institution=self.inst_a,
            session=self.session,
            name='First Semester',
            start_date=date(2025, 9, 8),
            end_date=date(2026, 1, 20),
            is_current=True
        )

        # Faculty & Department
        self.faculty = Faculty.objects.create(institution=self.inst_a, name='Computing', code='SIT')
        self.dept = Department.objects.create(institution=self.inst_a, faculty=self.faculty, name='Software Engineering', code='SE')
        self.course = Course.objects.create(institution=self.inst_a, department=self.dept, code='CSC301', title='OOSC', credit_units=3)

        self.offering = CourseOffering.objects.create(
            institution=self.inst_a,
            course=self.course,
            session=self.session,
            semester=self.semester
        )
        self.enrollment = Enrollment.objects.create(
            institution=self.inst_a,
            student=self.student,
            course_offering=self.offering
        )

    def test_custom_user_role_methods(self):
        """Tests User encapsulation methods for multi-tenant role checking."""
        self.assertTrue(self.student.is_student_in(self.inst_a))
        self.assertFalse(self.student.is_instructor_in(self.inst_a))
        self.assertIsNone(self.student.get_role_in(self.inst_b))

    def test_score_validation_guards(self):
        """Tests that negative scores and scores exceeding max_score raise ValidationErrors."""
        assessment = Assessment.objects.create(
            institution=self.inst_a,
            course_offering=self.offering,
            title='Midterm',
            max_score=50.0
        )

        # Valid score
        res = AssessmentResult.objects.create(
            institution=self.inst_a,
            assessment=assessment,
            student=self.student,
            score=42.0
        )
        self.assertEqual(res.percentage, 84.0)

        # Negative score should fail validation
        with self.assertRaises(ValidationError):
            bad_res = AssessmentResult(
                institution=self.inst_a,
                assessment=assessment,
                student=self.student,
                score=-5.0
            )
            bad_res.clean()

        # Score exceeding maximum should fail validation
        with self.assertRaises(ValidationError):
            over_res = AssessmentResult(
                institution=self.inst_a,
                assessment=assessment,
                student=self.student,
                score=55.0
            )
            over_res.clean()

    def test_attendance_calculation_encapsulation(self):
        """Tests enrollment attendance rate calculation."""
        cs1 = ClassSession.objects.create(institution=self.inst_a, course_offering=self.offering, session_date=date(2025, 9, 10))
        cs2 = ClassSession.objects.create(institution=self.inst_a, course_offering=self.offering, session_date=date(2025, 9, 15))

        AttendanceRecord.objects.create(institution=self.inst_a, class_session=cs1, student=self.student, status=AttendanceStatus.PRESENT)
        AttendanceRecord.objects.create(institution=self.inst_a, class_session=cs2, student=self.student, status=AttendanceStatus.ABSENT)

        # 1 out of 2 = 50.0%
        att_rate = self.enrollment.get_attendance_percentage()
        self.assertEqual(att_rate, 50.0)

    def test_tenant_data_isolation(self):
        """Verifies that courses and students created in Institution A do not appear in Institution B."""
        # Query under Institution B
        inst_b_courses = Course.objects.filter(institution=self.inst_b)
        self.assertEqual(inst_b_courses.count(), 0)

        inst_a_courses = Course.objects.filter(institution=self.inst_a)
        self.assertEqual(inst_a_courses.count(), 1)

    def test_machine_learning_service_inference(self):
        """Tests that RiskPredictionService produces valid predictions, factors, and recommendations."""
        # The service refuses to predict from defaults alone: record real
        # assessment evidence for the semester first.
        assessment = Assessment.objects.create(
            institution=self.inst_a,
            course_offering=self.offering,
            title='Quiz 1',
            assessment_type=AssessmentType.QUIZ,
            max_score=20.0,
        )
        AssessmentResult.objects.create(
            institution=self.inst_a,
            assessment=assessment,
            student=self.student,
            score=14.0,
        )

        pred = RiskPredictionService.evaluate_and_predict(self.student, self.semester, self.inst_a)
        self.assertIsNotNone(pred)
        self.assertIn(pred.risk_level, ['LOW', 'MEDIUM', 'HIGH'])
        self.assertGreaterEqual(pred.risk_score, 0.0)
        self.assertLessEqual(pred.risk_score, 1.0)
        self.assertIsInstance(pred.contributing_factors, list)
        self.assertIsInstance(pred.recommendations, list)
        # The stored snapshot must disclose how much real data backed the
        # estimate, and must not include the final-exam outcome.
        coverage = pred.feature_snapshot['data_coverage']
        self.assertEqual(coverage['assessment_results'], 1)
        self.assertEqual(coverage['active_enrollments'], 1)
