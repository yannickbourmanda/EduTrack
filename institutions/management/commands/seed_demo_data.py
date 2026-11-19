"""
EduTrack Seed Demo Data Management Command
Populates two realistic university tenants (American University of Nigeria and University of Lagos)
with faculties, departments, courses, course offerings, attendance records, assessments,
adviser interventions, and machine-learning risk predictions.
"""

from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from accounts.models import Membership, Role, MembershipStatus
from institutions.models import Institution, Faculty, Department, Programme
from academics.models import AcademicSession, Semester, Course, CourseOffering, Enrollment
from monitoring.models import GradingScale, Assessment, AssessmentType, AssessmentResult, ClassSession, AttendanceRecord, AttendanceStatus, ParticipationRecord
from advising.models import AdviserAssignment, AcademicIntervention, InterventionType, InterventionStatus
from predictions.services import RiskPredictionService

User = get_user_model()


class Command(BaseCommand):
    help = 'Seeds realistic multi-institution demonstration data for EduTrack'

    def handle(self, *args, **options):
        self.stdout.write("--- Seeding EduTrack Multi-Institution Demonstration Data ---")

        # 1. Superuser / Platform Administrator
        platform_admin, _ = User.objects.get_or_create(
            email='platform.admin@edutrack.edu',
            defaults={
                'username': 'platform.admin@edutrack.edu',
                'first_name': 'Dr. Aliyu',
                'last_name': 'Mohammed',
                'is_staff': True,
                'is_superuser': True,
                'is_email_verified': True,
            }
        )
        platform_admin.set_password('Password123!')
        platform_admin.save()
        self.stdout.write(f"Platform Admin: {platform_admin.email}")

        # 2. Institution A: American University of Nigeria (AUN)
        aun, _ = Institution.objects.get_or_create(
            code='AUN',
            defaults={
                'name': 'American University of Nigeria',
                'slug': 'aun',
                'contact_email': 'info@aun.edu.ng',
                'contact_phone': '+234 805 200 0000',
                'address': 'Lamido Zubairu Way, Yola, Adamawa State, Nigeria',
                'website': 'https://www.aun.edu.ng',
                'is_active': True,
            }
        )

        # 3. Institution B: University of Lagos (UNILAG)
        unilag, _ = Institution.objects.get_or_create(
            code='UNILAG',
            defaults={
                'name': 'University of Lagos',
                'slug': 'unilag',
                'contact_email': 'records@unilag.edu.ng',
                'contact_phone': '+234 1 280 2439',
                'address': 'Akoka, Yaba, Lagos State, Nigeria',
                'website': 'https://unilag.edu.ng',
                'is_active': True,
            }
        )

        # 4. Grading Scales for AUN (Standard 4.0/5.0 system)
        aun_grades = [
            ('A', 80.0, 100.0, 5.0, 'Excellent'),
            ('B', 70.0, 79.99, 4.0, 'Very Good'),
            ('C', 60.0, 69.99, 3.0, 'Good / Satisfactory'),
            ('D', 50.0, 59.99, 2.0, 'Pass'),
            ('F', 0.0, 49.99, 0.0, 'Fail / Academic Difficulty'),
        ]
        for grade, min_s, max_s, gp, desc in aun_grades:
            GradingScale.objects.get_or_create(
                institution=aun, grade=grade,
                defaults={'min_score': min_s, 'max_score': max_s, 'grade_point': gp, 'description': desc}
            )

        # 5. AUN Academic Structure
        faculty_sit, _ = Faculty.objects.get_or_create(
            institution=aun, code='SITC',
            defaults={'name': 'School of Information Technology and Computing'}
        )
        dept_se, _ = Department.objects.get_or_create(
            institution=aun, code='SWE',
            defaults={'faculty': faculty_sit, 'name': 'Department of Software Engineering'}
        )
        prog_se, _ = Programme.objects.get_or_create(
            institution=aun, code='B.Sc. SWE',
            defaults={'department': dept_se, 'name': 'B.Sc. Software Engineering', 'degree_type': 'BSC', 'duration_years': 4}
        )

        # 6. AUN Academic Session & Semester
        session_2526, _ = AcademicSession.objects.get_or_create(
            institution=aun, name='2025/2026',
            defaults={
                'start_date': date(2025, 9, 1),
                'end_date': date(2026, 6, 30),
                'is_current': True
            }
        )
        sem_first, _ = Semester.objects.get_or_create(
            institution=aun, session=session_2526, term=Semester.TermChoices.FIRST,
            defaults={
                'name': 'Harmattan / First Semester 2025/2026',
                'start_date': date(2025, 9, 8),
                'end_date': date(2026, 1, 20),
                'is_current': True
            }
        )

        # 7. AUN Courses
        course_oosc, _ = Course.objects.get_or_create(
            institution=aun, code='CSC301',
            defaults={
                'department': dept_se,
                'title': 'Object-Oriented Software Construction',
                'credit_units': 3,
                'description': 'Advanced principles of encapsulation, abstraction, design patterns, testing, and clean architecture.'
            }
        )
        course_arch, _ = Course.objects.get_or_create(
            institution=aun, code='SWE305',
            defaults={
                'department': dept_se,
                'title': 'Software Architecture & Design',
                'credit_units': 3,
                'description': 'Architectural styles, modular monoliths, multi-tenancy, and cloud design.'
            }
        )
        course_math, _ = Course.objects.get_or_create(
            institution=aun, code='MAT201',
            defaults={
                'department': dept_se,
                'title': 'Linear Algebra & Discrete Structures',
                'credit_units': 3,
                'description': 'Matrix theory, eigenvalues, discrete mathematical foundations of computer science.'
            }
        )

        # 8. Personas & Institutional Memberships
        def make_user(email, first, last, role, inst, dept=None, prog=None, matric=None, staff_id=None):
            u, _ = User.objects.get_or_create(
                email=email,
                defaults={
                    'username': email,
                    'first_name': first,
                    'last_name': last,
                    'matriculation_number': matric,
                    'staff_id': staff_id,
                    'is_email_verified': True,
                }
            )
            u.set_password('Password123!')
            u.save()

            m, _ = Membership.objects.get_or_create(
                user=u,
                institution=inst,
                defaults={
                    'role': role,
                    'status': MembershipStatus.APPROVED,
                    'department': dept,
                    'programme': prog,
                    'reviewed_at': timezone.now()
                }
            )
            return u, m

        # AUN Personas
        inst_admin, _ = make_user('admin@aun.edu.ng', 'Fatima', 'Danjuma', Role.INSTITUTION_ADMIN, aun, staff_id='STF-ADM-01')
        registrar, _ = make_user('registrar@aun.edu.ng', 'Babatunde', 'Ojo', Role.REGISTRAR, aun, staff_id='STF-REG-01')
        lecturer, _ = make_user('dr.ibrahim@aun.edu.ng', 'Dr. Ibrahim', 'Suleiman', Role.INSTRUCTOR, aun, dept=dept_se, staff_id='STF-FAC-102')
        adviser, _ = make_user('dr.chioma@aun.edu.ng', 'Dr. Chioma', 'Nnamdi', Role.ADVISER, aun, dept=dept_se, staff_id='STF-ADV-045')

        # Students (One high-risk, one good-standing, one moderate-risk)
        student_at_risk, _ = make_user(
            'emmanuel.okafor@aun.edu.ng', 'Emmanuel', 'Okafor', Role.STUDENT, aun,
            dept=dept_se, prog=prog_se, matric='A00024911'
        )
        student_good, _ = make_user(
            'zainab.bello@aun.edu.ng', 'Zainab', 'Bello', Role.STUDENT, aun,
            dept=dept_se, prog=prog_se, matric='A00023842'
        )
        student_moderate, _ = make_user(
            'kalu.uche@aun.edu.ng', 'Kalu', 'Uche', Role.STUDENT, aun,
            dept=dept_se, prog=prog_se, matric='A00025109'
        )

        # UNILAG Personas (To verify tenant isolation!)
        unilag_admin, _ = make_user('admin@unilag.edu.ng', 'Prof. Adeyemi', 'Lawal', Role.INSTITUTION_ADMIN, unilag, staff_id='UL-ADM-01')
        unilag_student, _ = make_user('folake.ade@unilag.edu.ng', 'Folake', 'Ade', Role.STUDENT, unilag, matric='UL/2023/5041')

        # 9. Course Offerings in AUN
        offering_oosc, _ = CourseOffering.objects.get_or_create(
            institution=aun, course=course_oosc, session=session_2526, semester=sem_first,
            defaults={'instructor': lecturer, 'syllabus': 'CSC301 Object-Oriented Software Construction syllabus.'}
        )
        offering_arch, _ = CourseOffering.objects.get_or_create(
            institution=aun, course=course_arch, session=session_2526, semester=sem_first,
            defaults={'instructor': lecturer, 'syllabus': 'SWE305 Architecture and Design patterns.'}
        )
        offering_math, _ = CourseOffering.objects.get_or_create(
            institution=aun, course=course_math, session=session_2526, semester=sem_first,
            defaults={'instructor': lecturer, 'syllabus': 'MAT201 Linear Algebra syllabus.'}
        )

        # 10. Enrollments
        for st in [student_at_risk, student_good, student_moderate]:
            Enrollment.objects.get_or_create(institution=aun, student=st, course_offering=offering_oosc)
            Enrollment.objects.get_or_create(institution=aun, student=st, course_offering=offering_arch)
            Enrollment.objects.get_or_create(institution=aun, student=st, course_offering=offering_math)

        # 11. Adviser Assignments
        AdviserAssignment.objects.get_or_create(
            institution=aun, adviser=adviser, student=student_at_risk,
            defaults={'notes': 'Academic monitoring assigned due to low entry quiz performance.'}
        )
        AdviserAssignment.objects.get_or_create(
            institution=aun, adviser=adviser, student=student_moderate,
            defaults={'notes': 'General academic progression review.'}
        )

        # 12. Class Sessions & Attendance (Generate 6 class lectures)
        today = date.today()
        for i in range(6):
            session_date = today - timedelta(days=(6 - i) * 5)
            cs, _ = ClassSession.objects.get_or_create(
                institution=aun, course_offering=offering_oosc, session_date=session_date,
                defaults={'topic': f'Module {i+1}: Advanced OOSC Patterns and Architecture'}
            )

            # Emmanuel (At-risk): Only attended 2 of 6 (33% attendance)
            status_emmanuel = AttendanceStatus.PRESENT if i in [0, 4] else AttendanceStatus.ABSENT
            AttendanceRecord.objects.get_or_create(
                institution=aun, class_session=cs, student=student_at_risk,
                defaults={'status': status_emmanuel, 'notes': 'Unexcused absence' if status_emmanuel == AttendanceStatus.ABSENT else 'Present'}
            )
            ParticipationRecord.objects.get_or_create(
                institution=aun, class_session=cs, student=student_at_risk,
                defaults={'score': 2 if status_emmanuel == AttendanceStatus.PRESENT else 1}
            )

            # Zainab (Good standing): Attended all 6 (100% attendance)
            AttendanceRecord.objects.get_or_create(
                institution=aun, class_session=cs, student=student_good,
                defaults={'status': AttendanceStatus.PRESENT, 'notes': 'On time'}
            )
            ParticipationRecord.objects.get_or_create(
                institution=aun, class_session=cs, student=student_good,
                defaults={'score': 5}
            )

            # Kalu (Moderate): Attended 4 of 6 (67% attendance)
            status_kalu = AttendanceStatus.PRESENT if i in [0, 1, 3, 5] else AttendanceStatus.LATE
            AttendanceRecord.objects.get_or_create(
                institution=aun, class_session=cs, student=student_moderate,
                defaults={'status': status_kalu, 'notes': 'Arrived 10 mins late' if status_kalu == AttendanceStatus.LATE else 'Present'}
            )
            ParticipationRecord.objects.get_or_create(
                institution=aun, class_session=cs, student=student_moderate,
                defaults={'score': 3}
            )

        # 13. Assessments for CSC301
        ass_quiz1, _ = Assessment.objects.get_or_create(
            institution=aun, course_offering=offering_oosc, title='Quiz 1: Encapsulation & Polymorphism',
            defaults={'assessment_type': AssessmentType.QUIZ, 'max_score': 20.0, 'weight_percentage': 10.0, 'due_date': today - timedelta(days=25)}
        )
        ass_midterm, _ = Assessment.objects.get_or_create(
            institution=aun, course_offering=offering_oosc, title='Mid-Semester Examination',
            defaults={'assessment_type': AssessmentType.MIDTERM, 'max_score': 100.0, 'weight_percentage': 30.0, 'due_date': today - timedelta(days=12)}
        )
        ass_project, _ = Assessment.objects.get_or_create(
            institution=aun, course_offering=offering_oosc, title='Milestone Project: Modular Monolith',
            defaults={'assessment_type': AssessmentType.PROJECT, 'max_score': 50.0, 'weight_percentage': 20.0, 'due_date': today + timedelta(days=10)}
        )

        # Scores for Emmanuel (At risk: 7/20 on Quiz 1 = 35%, 38/100 on Midterm = 38%)
        AssessmentResult.objects.get_or_create(
            institution=aun, assessment=ass_quiz1, student=student_at_risk,
            defaults={'score': 7.0, 'graded_by': lecturer, 'feedback': 'Struggled with class inheritance and method overriding.'}
        )
        AssessmentResult.objects.get_or_create(
            institution=aun, assessment=ass_midterm, student=student_at_risk,
            defaults={'score': 38.0, 'graded_by': lecturer, 'feedback': 'Did not answer design pattern questions. Urgent revision required.'}
        )

        # Scores for Zainab (Good: 19/20 on Quiz 1 = 95%, 92/100 on Midterm = 92%)
        AssessmentResult.objects.get_or_create(
            institution=aun, assessment=ass_quiz1, student=student_good,
            defaults={'score': 19.0, 'graded_by': lecturer, 'feedback': 'Superb answer demonstrating decoupling and clean boundaries.'}
        )
        AssessmentResult.objects.get_or_create(
            institution=aun, assessment=ass_midterm, student=student_good,
            defaults={'score': 92.0, 'graded_by': lecturer, 'feedback': 'Excellent conceptual clarity and diagrams.'}
        )

        # Scores for Kalu (Moderate: 13/20 on Quiz 1 = 65%, 62/100 on Midterm = 62%)
        AssessmentResult.objects.get_or_create(
            institution=aun, assessment=ass_quiz1, student=student_moderate,
            defaults={'score': 13.0, 'graded_by': lecturer, 'feedback': 'Good attempt, review Liskov substitution principle.'}
        )
        AssessmentResult.objects.get_or_create(
            institution=aun, assessment=ass_midterm, student=student_moderate,
            defaults={'score': 62.0, 'graded_by': lecturer, 'feedback': 'Satisfactory performance.'}
        )

        # 14. Academic Intervention initiated by Adviser for Emmanuel
        AcademicIntervention.objects.get_or_create(
            institution=aun, student=student_at_risk, adviser=adviser,
            intervention_type=InterventionType.COUNSELLING,
            defaults={
                'status': InterventionStatus.IN_PROGRESS,
                'reason': 'Triggered by high risk early warning: 33% attendance drop and failing midterm score (38%) in CSC301.',
                'action_taken': 'Met with Emmanuel. Identified accommodation transportation challenges. Arranged peer tutoring with Zainab Bello. Agreed on minimum 80% lecture attendance.',
                'date_conducted': today - timedelta(days=5),
                'follow_up_date': today + timedelta(days=7),
            }
        )

        # 15. Generate ML Risk Predictions for All Students
        self.stdout.write("Running ML Risk Prediction Service for enrolled students...")
        for st in [student_at_risk, student_good, student_moderate]:
            pred = RiskPredictionService.evaluate_and_predict(st, sem_first, aun)
            if pred:
                self.stdout.write(f"  -> {st.get_full_name()}: {pred.get_risk_level_display()} (Risk Score: {pred.risk_score * 100:.1f}%)")

        self.stdout.write(self.style.SUCCESS("EduTrack demonstration data seeded successfully!"))
