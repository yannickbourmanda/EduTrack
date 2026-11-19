"""
Predictions Service - EduTrack
Decoupled machine learning service for academic difficulty estimation.

OOSC Principles Applied:
- Service Layer Pattern (Abstraction): Encapsulates feature extraction, scaling,
  model inference, and explainability reporting from controllers/views.
- Ethical Guardrails: Explicitly tags all estimates with advisory disclaimers.
"""

import os
import json
import joblib
import numpy as np
from django.conf import settings
from django.db import transaction

try:
    import pandas as pd
except ImportError:  # pragma: no cover - pandas is optional at runtime
    pd = None

from .models import RiskPrediction, RiskLevel
from academics.models import Enrollment, Semester
from monitoring.models import (
    AssessmentResult,
    AssessmentType,
    AttendanceRecord,
    AttendanceStatus,
    ParticipationRecord,
)


class RiskPredictionService:
    """
    Singleton-style Machine Learning Inference Service.
    Loads serialized Random Forest model from disk and produces advisory predictions.
    """
    _model = None
    _metrics = None

    @classmethod
    def get_model(cls):
        if cls._model is None:
            model_path = os.path.join(settings.BASE_DIR, 'ml_artifacts', 'academic_risk_rf_v1.joblib')
            if os.path.exists(model_path):
                cls._model = joblib.load(model_path)
            else:
                return None
        return cls._model

    @classmethod
    def get_metrics(cls):
        if cls._metrics is None:
            metrics_path = os.path.join(settings.BASE_DIR, 'ml_artifacts', 'model_metrics_v1.json')
            if os.path.exists(metrics_path):
                with open(metrics_path, 'r') as f:
                    cls._metrics = json.load(f)
            else:
                cls._metrics = {}
        return cls._metrics

    @classmethod
    def extract_student_features(cls, student, semester, institution):
        """
        Extracts genuine academic metrics from the student's enrollments,
        assessments, attendance, and participation in the specified semester.

        Integrity rules:
        - Final examinations are excluded from feature extraction. They are
          outcome variables, so including them would leak the prediction
          target into the inputs.
        - The returned dict carries a ``data_coverage`` block stating how many
          real records back each signal group, so a reader can see which
          features were genuine and which fell back to a neutral default.

        Returns ``None`` when the student has no active enrollment in the
        semester (nothing to advise on).
        """
        enrollments = Enrollment.objects.filter(
            student=student,
            course_offering__semester=semester,
            institution=institution,
            status=Enrollment.EnrollmentStatus.ACTIVE
        ).select_related('course_offering__course')

        enrollment_count = enrollments.count()
        if not enrollment_count:
            return None

        # 1. Attendance Rate
        total_attendance_records = AttendanceRecord.objects.filter(
            student=student,
            class_session__course_offering__semester=semester,
            institution=institution
        )
        attendance_record_count = total_attendance_records.count()
        if attendance_record_count:
            present_count = total_attendance_records.filter(
                status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.EXCUSED]
            ).count()
            attendance_rate = (present_count / attendance_record_count) * 100
        else:
            attendance_rate = 85.0  # Baseline default if no attendance taken yet

        # 2. Continuous Assessment Average & Quizzes
        # Final examinations are excluded: an exam result is an *outcome* of
        # the period being predicted, not an input to it (target leakage).
        assessments_taken = AssessmentResult.objects.filter(
            student=student,
            assessment__course_offering__semester=semester,
            institution=institution,
        ).exclude(
            assessment__assessment_type=AssessmentType.FINAL_EXAM,
        ).select_related('assessment')

        assessment_result_count = assessments_taken.count()
        if assessment_result_count:
            scores = [r.percentage for r in assessments_taken]
            assessment_avg = sum(scores) / len(scores)

            quizzes = [r.percentage for r in assessments_taken if r.assessment.assessment_type == 'QUIZ']
            quiz_avg = sum(quizzes) / len(quizzes) if quizzes else assessment_avg

            midterms = [r.percentage for r in assessments_taken if r.assessment.assessment_type == 'MIDTERM']
            midterm_score = sum(midterms) / len(midterms) if midterms else assessment_avg

            failed_assessments = sum(1 for s in scores if s < 50.0)
        else:
            assessment_avg = 70.0
            quiz_avg = 70.0
            midterm_score = 70.0
            failed_assessments = 0

        # 3. Participation Average
        participation_records = ParticipationRecord.objects.filter(
            student=student,
            class_session__course_offering__semester=semester,
            institution=institution
        )
        participation_record_count = participation_records.count()
        if participation_record_count:
            participation_score = sum(p.score for p in participation_records) / participation_record_count
        else:
            participation_score = 3.5

        # 4. Total Credits
        credit_load = sum(e.course_offering.course.credit_units for e in enrollments)

        # 5. Prior GPA (from previous completed enrollments)
        completed_enrollments = Enrollment.objects.filter(
            student=student,
            institution=institution,
            grade_point__isnull=False
        ).exclude(course_offering__semester=semester)

        prior_gpa_recorded = completed_enrollments.exists()
        if prior_gpa_recorded:
            total_pts = sum(e.grade_point * e.course_offering.course.credit_units for e in completed_enrollments)
            total_crs = sum(e.course_offering.course.credit_units for e in completed_enrollments)
            prior_gpa = round(total_pts / total_crs, 2) if total_crs > 0 else 3.0
        else:
            prior_gpa = 3.2

        return {
            'attendance_rate': round(float(attendance_rate), 1),
            'assessment_avg': round(float(assessment_avg), 1),
            'quiz_avg': round(float(quiz_avg), 1),
            'midterm_score': round(float(midterm_score), 1),
            'participation_score': round(float(participation_score), 1),
            'failed_assessments_count': int(failed_assessments),
            'prior_gpa': round(float(prior_gpa), 2),
            'credit_load': int(credit_load),
            'data_coverage': {
                'active_enrollments': enrollment_count,
                'assessment_results': assessment_result_count,
                'attendance_records': attendance_record_count,
                'participation_records': participation_record_count,
                'prior_gpa_available': prior_gpa_recorded,
            },
        }

    @classmethod
    def evaluate_and_predict(cls, student, semester, institution):
        """
        Runs ML model inference on the student's real semester performance,
        constructs explainability reasoning and tailored intervention recommendations,
        and saves a RiskPrediction record.

        Integrity guardrails:
        - Returns ``None`` when there is no active enrollment or when the
          semester has no recorded assessment evidence at all, so a prediction
          is never fabricated purely from neutral defaults.
        - Feature values are ordered with ``model.feature_names_in_`` when the
          artifact exposes them, so column order can never drift from the
          order used at training time.
        - Deactivating previous predictions and persisting the new one happen
          inside a single transaction.
        """
        features = cls.extract_student_features(student, semester, institution)
        if not features:
            return None

        coverage = features['data_coverage']
        if not coverage['assessment_results']:
            # Without any recorded assessment result the core signal would be
            # the neutral default (70%), not the student's actual coursework.
            return None

        model = cls.get_model()
        numeric_features = {
            key: value for key, value in features.items()
            if key != 'data_coverage'
        }

        feature_names = (
            [str(name) for name in model.feature_names_in_]
            if model is not None and hasattr(model, 'feature_names_in_')
            else None
        )
        if feature_names is not None:
            missing = [name for name in feature_names if name not in numeric_features]
            if missing:
                # The stored artifact expects signals this extraction no
                # longer provides; refusing beats guessing an order.
                return None
            feature_values = [numeric_features[name] for name in feature_names]
        else:
            feature_values = [
                numeric_features['attendance_rate'],
                numeric_features['assessment_avg'],
                numeric_features['quiz_avg'],
                numeric_features['midterm_score'],
                numeric_features['participation_score'],
                numeric_features['failed_assessments_count'],
                numeric_features['prior_gpa'],
                numeric_features['credit_load'],
            ]

        # Infer class & probability
        if model is not None:
            if feature_names is not None and pd is not None:
                # Name-labelled input: scikit-learn then verifies the column
                # order against the training artifact instead of trusting it.
                X = pd.DataFrame([feature_values], columns=feature_names)
            else:
                X = np.array([feature_values])
            pred_class = model.predict(X)[0]
            if hasattr(model, 'predict_proba'):
                probas = model.predict_proba(X)[0]
                # Probability of difficulty (medium or high risk)
                risk_score = float(probas[1] * 0.5 + probas[2] * 1.0)
            else:
                risk_score = 0.85 if pred_class == 2 else (0.5 if pred_class == 1 else 0.15)
        else:
            # Fallback heuristic rule
            pred_class = 2 if numeric_features['assessment_avg'] < 50 or numeric_features['attendance_rate'] < 60 else (1 if numeric_features['assessment_avg'] < 65 else 0)
            risk_score = 0.80 if pred_class == 2 else (0.45 if pred_class == 1 else 0.15)

        # Map to Risk Level
        if pred_class == 2 or risk_score >= 0.65:
            risk_level = RiskLevel.HIGH
        elif pred_class == 1 or risk_score >= 0.40:
            risk_level = RiskLevel.MEDIUM
        else:
            risk_level = RiskLevel.LOW

        # Generate Explainability: Key Contributing Factors
        factors = []
        if features['attendance_rate'] < 70:
            factors.append(f"Low overall lecture attendance: {features['attendance_rate']}% (institutional minimum is 75%).")
        if features['failed_assessments_count'] > 0:
            factors.append(f"Recorded {features['failed_assessments_count']} assessment(s) scoring below passing threshold (<50%).")
        if features['assessment_avg'] < 55:
            factors.append(f"Continuous assessment average is currently low at {features['assessment_avg']}%.")
        if features['midterm_score'] < 50:
            factors.append(f"Mid-semester examination mark was {features['midterm_score']}%.")
        if features['participation_score'] <= 2.5:
            factors.append(f"Classroom interaction rating is below average ({features['participation_score']}/5.0).")
        if not factors:
            factors.append("Consistent lecture attendance, stable quiz performance, and satisfactory grade standing.")

        # Generate Evidence-Based Academic Recommendations
        recommendations = []
        if risk_level == RiskLevel.HIGH:
            recommendations.append("Immediate academic counselling session recommended with designated faculty adviser.")
            recommendations.append("Enroll in departmental peer-tutoring and supplementary study clinics.")
            recommendations.append("Schedule one-on-one office hour consultations with course lecturers for weak subjects.")
        elif risk_level == RiskLevel.MEDIUM:
            recommendations.append("Prioritize upcoming continuous assessment assignments and test revisions.")
            recommendations.append("Maintain strict lecture attendance to prevent falling below examination eligibility threshold.")
            recommendations.append("Consult academic adviser to review study habits and semester time allocation.")
        else:
            recommendations.append("Continue maintaining strong attendance, timely homework submission, and active participation.")
            recommendations.append("Explore honors project or departmental research group opportunities.")

        # Deactivate previous active predictions and persist the new one as a
        # single atomic unit, so a failure cannot leave two current estimates.
        with transaction.atomic():
            RiskPrediction.objects.filter(
                student=student,
                semester=semester,
                institution=institution,
                is_current=True
            ).update(is_current=False)

            # Store the numeric feature vector plus the data-coverage block so
            # an auditor can see which inputs were genuine records and which
            # fell back to neutral defaults.
            snapshot = dict(features)

            prediction = RiskPrediction.objects.create(
                institution=institution,
                student=student,
                semester=semester,
                risk_level=risk_level,
                risk_score=round(risk_score, 3),
                model_version='rf_academic_v1.0',
                model_name='Random Forest Early Warning Classifier',
                contributing_factors=factors,
                recommendations=recommendations,
                feature_snapshot=snapshot,
                is_current=True
            )

        return prediction
