from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from dashboard import analytics
from institutions.models import Institution


class DemoChartDataTests(TestCase):
    def test_seeded_student_trends_have_chronological_variable_data(self):
        call_command('seed_demo_data', stdout=StringIO())
        student = get_user_model().objects.get(
            email='emmanuel.okafor@aun.edu.ng'
        )
        institution = Institution.objects.get(code='AUN')

        performance = analytics.performance_by_semester(
            [student.id], institution
        )
        attendance = analytics.attendance_by_semester(
            [student.id], institution
        )
        assessments = analytics.assessment_progress(student, institution)
        _, _, course_scores = analytics.student_course_rows(
            student, institution, active_only=False
        )
        score_distribution = analytics.score_band_distribution(course_scores)

        self.assertEqual(len(performance), 4)
        self.assertEqual(len(attendance), 4)
        self.assertGreater(
            len({point['value'] for point in performance}), 1
        )
        self.assertGreater(
            len({point['value'] for point in attendance}), 1
        )
        self.assertEqual(len(assessments['values']), 8)
        self.assertGreater(len(set(assessments['values'])), 1)
        self.assertEqual(score_distribution['values'], [0, 2, 0, 1, 1])
        self.assertIn('2022/2023', performance[0]['label'])
        self.assertIn('2025/2026', performance[-1]['label'])
