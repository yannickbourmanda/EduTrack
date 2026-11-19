"""
Academic analytics helpers for EduTrack.

Everything in this module is derived from stored database records. No random or
fabricated values are produced. When there is not enough data for a metric the
helper returns ``None`` (or an empty series) so the interface can show an
honest empty state instead of a misleading zero or 100% default.
"""

from statistics import mean

from django.db.models import Count

from academics.models import (
    AcademicSession,
    Course,
    CourseOffering,
    Enrollment,
    Semester,
)
from advising.models import AcademicIntervention, AdviserAssignment, InterventionStatus
from institutions.models import Department, Programme
from monitoring.models import AssessmentResult, AttendanceRecord, AttendanceStatus
from predictions.models import RiskLevel, RiskPrediction

ATTENDED_STATUSES = (
    AttendanceStatus.PRESENT,
    AttendanceStatus.LATE,
    AttendanceStatus.EXCUSED,
)

SCORE_BANDS = [
    ('70–100', 70, 101, '#1f6f54'),
    ('60–69', 60, 70, '#3d7f63'),
    ('50–59', 50, 60, '#b8860b'),
    ('40–49', 40, 50, '#b45309'),
    ('0–39', 0, 40, '#b91c1c'),
]

# ---------------------------------------------------------------------------
# Documented academic thresholds (single source of truth).
#
# LOW_PERFORMANCE_THRESHOLD — recorded performance below 50% has been shown as
#   "needs review" / red on the course standing badges since Phase 4; the same
#   cut-off is used for the attention summaries so the interface stays
#   consistent.
# LOW_ATTENDANCE_THRESHOLD — attendance below 75% has been flagged red by the
#   same dashboards (a typical institutional minimum-attendance requirement).
#   Both values mirror behaviour that already existed in the templates; they
#   are centralised here so reports and dashboards cannot drift apart.
# MIN_CLASS_GROUP_SIZE — smallest number of recorded results for which a single
#   class average is published. Below it the average is suppressed ("Insufficient
#   data") so a small class cannot be reduced to one or two identifiable
#   students. Department-level averages pool several classes and are not
#   suppressed; aggregate counts (enrolments, participation, attendance) are
#   never suppressed because they do not expose individual scores.
# ---------------------------------------------------------------------------
LOW_PERFORMANCE_THRESHOLD = 50.0
LOW_ATTENDANCE_THRESHOLD = 75.0
MIN_CLASS_GROUP_SIZE = 5


def _mean(values):
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return round(mean(clean), 1)


def _percentage(value, maximum):
    try:
        value = float(value)
        maximum = float(maximum)
    except (TypeError, ValueError):
        return None
    if maximum <= 0:
        return None
    return round((value / maximum) * 100, 1)


def assessment_performance_map(institution, student_ids=None):
    """
    Return ``{(offering_id, student_id): weighted_average_percentage}`` from
    recorded assessment results.

    Each recorded result is weighted by its assessment's
    ``weight_percentage`` so a heavily weighted midterm counts more than a
    light weekly quiz, matching how the official grade is composed. One query,
    no per-row database access.
    """
    queryset = AssessmentResult.objects.filter(institution=institution)
    if student_ids is not None:
        if not student_ids:
            return {}
        queryset = queryset.filter(student_id__in=student_ids)

    buckets = {}
    values = queryset.values(
        'assessment__course_offering_id',
        'student_id',
        'score',
        'assessment__max_score',
        'assessment__weight_percentage',
    )
    for row in values:
        pct = _percentage(row['score'], row['assessment__max_score'])
        if pct is None:
            continue
        try:
            weight = float(row['assessment__weight_percentage'])
        except (TypeError, ValueError):
            weight = 0.0
        key = (row['assessment__course_offering_id'], row['student_id'])
        bucket = buckets.setdefault(key, {'weighted': 0.0, 'weights': 0.0, 'simple': []})
        if weight > 0:
            bucket['weighted'] += pct * weight
            bucket['weights'] += weight
        # Kept so a group whose weights are all zero still yields an honest
        # unweighted mean instead of looking unscored.
        bucket['simple'].append(pct)

    def _bucket_average(bucket):
        if bucket['weights'] > 0:
            return round(bucket['weighted'] / bucket['weights'], 1)
        return _mean(bucket['simple'])

    return {key: _bucket_average(bucket) for key, bucket in buckets.items()}


def attendance_map(institution, student_ids=None, semester=None):
    """Return per-enrollment attendance counts and rates in one query."""
    queryset = AttendanceRecord.objects.filter(institution=institution)
    if semester is not None:
        queryset = queryset.filter(
            class_session__course_offering__semester=semester,
        )
    if student_ids is not None:
        if not student_ids:
            return {}, {'total': 0, 'attended': 0, 'rate': None}
        queryset = queryset.filter(student_id__in=student_ids)

    buckets = {}
    for row in queryset.values(
        'class_session__course_offering_id', 'student_id', 'status'
    ):
        key = (row['class_session__course_offering_id'], row['student_id'])
        bucket = buckets.setdefault(key, {'total': 0, 'attended': 0})
        bucket['total'] += 1
        if row['status'] in ATTENDED_STATUSES:
            bucket['attended'] += 1

    rates = {}
    grand_total = 0
    grand_attended = 0
    for key, bucket in buckets.items():
        rates[key] = round(
            (bucket['attended'] / bucket['total']) * 100, 1
        ) if bucket['total'] else None
        grand_total += bucket['total']
        grand_attended += bucket['attended']

    overall = {
        'total': grand_total,
        'attended': grand_attended,
        'rate': round((grand_attended / grand_total) * 100, 1) if grand_total else None,
    }
    return rates, overall


def attendance_by_student(institution, student_ids):
    """Attendance rate per student, derived from recorded attendance."""
    if not student_ids:
        return {}, {'total': 0, 'attended': 0, 'rate': None}

    buckets = {}
    for row in AttendanceRecord.objects.filter(
        institution=institution,
        student_id__in=student_ids,
    ).values('student_id', 'status'):
        bucket = buckets.setdefault(row['student_id'], {'total': 0, 'attended': 0})
        bucket['total'] += 1
        if row['status'] in ATTENDED_STATUSES:
            bucket['attended'] += 1

    rates = {
        student_id: (
            round((counts['attended'] / counts['total']) * 100, 1)
            if counts['total'] else None
        )
        for student_id, counts in buckets.items()
    }
    total = sum(counts['total'] for counts in buckets.values())
    attended = sum(counts['attended'] for counts in buckets.values())
    return rates, {
        'total': total,
        'attended': attended,
        'rate': round((attended / total) * 100, 1) if total else None,
    }


def attendance_counts_by_offering(institution, offering_ids):
    """
    Attended/total attendance counts per offering, in one grouped query.

    Returns ``{offering_id: {'attended': int, 'total': int, 'rate': float|None}}``
    so callers can show class-level attendance and still roll the same counts up
    by department or instructor without a second scan. Record totals are
    aggregate counts and never identify individual students.
    """
    if not offering_ids:
        return {}

    buckets = {}
    rows = AttendanceRecord.objects.filter(
        institution=institution,
        class_session__course_offering_id__in=offering_ids,
    ).values('class_session__course_offering_id', 'status')
    for row in rows:
        offering_id = row['class_session__course_offering_id']
        bucket = buckets.setdefault(
            offering_id, {'attended': 0, 'total': 0}
        )
        bucket['total'] += 1
        if row['status'] in ATTENDED_STATUSES:
            bucket['attended'] += 1

    for bucket in buckets.values():
        bucket['rate'] = (
            round((bucket['attended'] / bucket['total']) * 100, 1)
            if bucket['total'] else None
        )
    return buckets


def enrollment_performance(enrollment, performance_map):
    """Official final score when present, otherwise recorded assessment average."""
    if enrollment.final_score is not None:
        return float(enrollment.final_score)
    return performance_map.get(
        (enrollment.course_offering_id, enrollment.student_id)
    )


def student_course_rows(student, institution, active_only=True, semester=None):
    """
    Course-by-course performance for one student, using stored records.

    ``semester`` optionally narrows the rows (and the attendance totals
    returned alongside them) to one semester, so a dashboard shows the
    current semester rather than the student's whole history.
    """
    queryset = Enrollment.objects.filter(
        student=student,
        institution=institution,
    )
    if semester is not None:
        queryset = queryset.filter(course_offering__semester=semester)
    queryset = queryset.select_related(
        'course_offering__course',
        'course_offering__instructor',
        'course_offering__semester__session',
    )
    if active_only:
        queryset = queryset.filter(status=Enrollment.EnrollmentStatus.ACTIVE)
    enrollments = list(queryset)

    performance_map = assessment_performance_map(institution, [student.id])
    attendance_rates, overall = attendance_map(
        institution, [student.id], semester=semester
    )

    rows = []
    for enrollment in enrollments:
        assessment_score = performance_map.get(
            (enrollment.course_offering_id, student.id)
        )
        performance = enrollment_performance(enrollment, performance_map)
        rows.append({
            'enrollment': enrollment,
            'offering': enrollment.course_offering,
            'course': enrollment.course_offering.course,
            'semester': enrollment.course_offering.semester,
            'assessment_score': assessment_score,
            'final_score': (
                float(enrollment.final_score)
                if enrollment.final_score is not None else None
            ),
            'final_grade': enrollment.final_grade,
            'grade_point': (
                float(enrollment.grade_point)
                if enrollment.grade_point is not None else None
            ),
            'performance': round(performance, 1) if performance is not None else None,
            'attendance_rate': attendance_rates.get(
                (enrollment.course_offering_id, student.id)
            ),
        })

    return rows, overall


def performance_by_semester(student_ids, institution):
    """Average recorded performance per semester for a set of students."""
    if not student_ids:
        return []

    enrollments = Enrollment.objects.filter(
        institution=institution,
        student_id__in=student_ids,
    ).select_related(
        'course_offering__semester__session'
    )
    performance_map = assessment_performance_map(institution, student_ids)

    buckets = {}
    for enrollment in enrollments:
        score = enrollment_performance(enrollment, performance_map)
        if score is None:
            continue
        semester = enrollment.course_offering.semester
        bucket = buckets.setdefault(
            semester.id,
            {'semester': semester, 'scores': [], 'count': 0},
        )
        bucket['scores'].append(score)
        bucket['count'] += 1

    ordered = sorted(
        buckets.values(),
        key=lambda item: (item['semester'].start_date, item['semester'].id),
    )
    return [
        {
            'label': f"{item['semester'].session.name} · {item['semester'].name}",
            'short_label': item['semester'].name,
            'value': _mean(item['scores']),
            'record_count': item['count'],
        }
        for item in ordered
    ]


def attendance_by_semester(student_ids, institution):
    """Attendance percentage per semester for a set of students."""
    if not student_ids:
        return []

    buckets = {}
    rows = AttendanceRecord.objects.filter(
        institution=institution,
        student_id__in=student_ids,
    ).values(
        'class_session__course_offering__semester_id',
        'class_session__course_offering__semester__name',
        'class_session__course_offering__semester__start_date',
        'class_session__course_offering__semester__session__name',
        'status',
    )
    for row in rows:
        semester_id = row['class_session__course_offering__semester_id']
        bucket = buckets.setdefault(semester_id, {
            'name': row['class_session__course_offering__semester__name'],
            'session': row['class_session__course_offering__semester__session__name'],
            'start': row['class_session__course_offering__semester__start_date'],
            'total': 0,
            'attended': 0,
        })
        bucket['total'] += 1
        if row['status'] in ATTENDED_STATUSES:
            bucket['attended'] += 1

    ordered = sorted(buckets.values(), key=lambda item: (item['start'], item['name']))
    return [
        {
            'label': f"{item['session']} · {item['name']}",
            'short_label': item['name'],
            'value': (
                round((item['attended'] / item['total']) * 100, 1)
                if item['total'] else None
            ),
            'record_count': item['total'],
        }
        for item in ordered
    ]


def score_band_distribution(scores):
    """Count recorded percentages into transparent score bands."""
    clean = [float(s) for s in scores if s is not None]
    labels = [band[0] for band in SCORE_BANDS]
    values = [0] * len(SCORE_BANDS)
    for score in clean:
        for index, (_, low, high, _) in enumerate(SCORE_BANDS):
            if low <= score < high:
                values[index] += 1
                break
    return {
        'labels': labels,
        'values': values,
        'colors': [band[3] for band in SCORE_BANDS],
        'total': len(clean),
    }


def recent_results(student, institution, limit=8):
    return (
        AssessmentResult.objects
        .filter(student=student, institution=institution)
        .select_related(
            'assessment__course_offering__course',
            'assessment__course_offering__semester',
        )
        .order_by('-graded_at')[:limit]
    )


def assessment_progress(student, institution, limit=12):
    results = (
        AssessmentResult.objects
        .filter(student=student, institution=institution)
        .select_related('assessment__course_offering__course')
        .order_by('graded_at', 'assessment__due_date')[:limit]
    )
    labels = []
    values = []
    for result in results:
        labels.append(
            f"{result.assessment.course_offering.course.code} · "
            f"{result.assessment.title}"
        )
        values.append(result.percentage)
    return {'labels': labels, 'values': values}


def attendance_trend_for_offerings(offerings, institution):
    """Attendance rate per recorded class-session date for given offerings."""
    offering_ids = [offering.id for offering in offerings]
    if not offering_ids:
        return []

    buckets = {}
    rows = AttendanceRecord.objects.filter(
        institution=institution,
        class_session__course_offering_id__in=offering_ids,
    ).values('class_session__session_date', 'status')
    for row in rows:
        bucket = buckets.setdefault(
            row['class_session__session_date'], {'total': 0, 'attended': 0}
        )
        bucket['total'] += 1
        if row['status'] in ATTENDED_STATUSES:
            bucket['attended'] += 1

    ordered = sorted(buckets.items())[-12:]
    return [
        {
            'label': day.strftime('%d %b'),
            'value': round((counts['attended'] / counts['total']) * 100, 1)
            if counts['total'] else None,
            'record_count': counts['total'],
        }
        for day, counts in ordered
    ]


def assessment_performance_for_offerings(
    offerings, institution, enrolled_counts=None
):
    """
    Average score percentage per assessment for a set of offerings.

    ``enrolled_counts`` optionally maps ``offering_id -> active enrolment
    count``. When supplied, each assessment also reports participation — how
    many of that offering's enrolled students were actually scored — which is
    an aggregate completion figure and never identifies individuals.
    """
    offering_ids = [offering.id for offering in offerings]
    if not offering_ids:
        return {'labels': [], 'values': [], 'meta': []}

    buckets = {}
    rows = AssessmentResult.objects.filter(
        institution=institution,
        assessment__course_offering_id__in=offering_ids,
    ).values(
        'assessment_id',
        'assessment__title',
        'assessment__course_offering__course__code',
        'assessment__course_offering_id',
        'score',
        'assessment__max_score',
    )
    for row in rows:
        percentage = _percentage(row['score'], row['assessment__max_score'])
        if percentage is None:
            continue
        bucket = buckets.setdefault(row['assessment_id'], {
            'label': (
                f"{row['assessment__course_offering__course__code']} · "
                f"{row['assessment__title']}"
            ),
            'offering_id': row['assessment__course_offering_id'],
            'scores': [],
        })
        bucket['scores'].append(percentage)

    ordered = list(buckets.values())[:10]
    labels = []
    values = []
    participation = []
    meta = []
    table_rows = []
    for item in ordered:
        labels.append(item['label'])
        values.append(_mean(item['scores']))
        scored = len(item['scores'])
        enrolled = (enrolled_counts or {}).get(item['offering_id'])
        if enrolled:
            participation.append(
                round((scored / enrolled) * 100, 1)
            )
            meta.append(f'{scored} of {enrolled} enrolled scored')
        else:
            participation.append(None)
            meta.append(f'{scored} result(s)')
        table_rows.append({
            'label': item['label'],
            'average': values[-1],
            'participation': (
                f'{scored} of {enrolled} enrolled scored'
                if enrolled else f'{scored} result(s)'
            ),
        })
    return {
        'labels': labels,
        'values': values,
        'meta': meta,
        'participation': participation,
        'table_rows': table_rows,
    }


def instructor_analytics(instructor, institution):
    offerings = list(
        CourseOffering.objects.filter(
            institution=institution,
            instructor=instructor,
        )
        .select_related('course', 'semester__session')
        # Assessment/session counts are computed by the database; one per
        # offering here would issue two extra queries for every course row.
        .annotate(
            assessments_count=Count('assessments', distinct=True),
            sessions_count=Count('class_sessions', distinct=True),
        )
        .order_by('course__code')
    )

    enrollments = list(
        Enrollment.objects.filter(
            institution=institution,
            course_offering_id__in=[offering.id for offering in offerings],
            status=Enrollment.EnrollmentStatus.ACTIVE,
        ).select_related('student', 'course_offering')
    )

    by_offering = {}
    for enrollment in enrollments:
        by_offering.setdefault(enrollment.course_offering_id, []).append(enrollment)

    student_ids = sorted({enrollment.student_id for enrollment in enrollments})
    performance_map = assessment_performance_map(institution, student_ids)

    offering_rows = []
    all_scores = []
    # Per-student aggregation across the instructor's own offerings; built
    # from the enrollment list already loaded above (no extra query).
    student_scores = {}
    for offering in offerings:
        rows = by_offering.get(offering.id, [])
        offering_scores = []
        for enrollment in rows:
            score = enrollment_performance(enrollment, performance_map)
            if score is not None:
                offering_scores.append(score)
                student_scores.setdefault(enrollment.student_id, []).append(score)
        all_scores.extend(offering_scores)
        offering_rows.append({
            'offering': offering,
            'enrolled_count': len(rows),
            'assessments_count': offering.assessments_count,
            'sessions_count': offering.sessions_count,
            'average': _mean(offering_scores),
            'scored_students': len(offering_scores),
        })

    _, attendance_overall = attendance_map(institution, student_ids)
    # One extra grouped query for per-student attendance across the
    # instructor's classes; the performance side reuses in-memory data.
    student_attendance, _ = attendance_by_student(institution, student_ids)

    student_rows_by_id = {
        enrollment.student_id: enrollment.student for enrollment in enrollments
    }
    student_averages = {
        student_id: _mean(scores)
        for student_id, scores in student_scores.items()
    }
    low_performance_ids = {
        student_id
        for student_id, average in student_averages.items()
        if average is not None and average < LOW_PERFORMANCE_THRESHOLD
    }
    low_attendance_ids = {
        student_id
        for student_id, rate in student_attendance.items()
        if rate is not None and rate < LOW_ATTENDANCE_THRESHOLD
    }
    attention_ids = low_performance_ids | low_attendance_ids
    attention_rows = []
    for student_id in attention_ids:
        average = student_averages.get(student_id)
        attendance_rate = student_attendance.get(student_id)
        attention_rows.append({
            'student': student_rows_by_id.get(student_id),
            'average': average,
            'attendance_rate': attendance_rate,
            'low_performance': student_id in low_performance_ids,
            'low_attendance': student_id in low_attendance_ids,
        })
    attention_rows.sort(key=lambda row: (
        row['average'] if row['average'] is not None else LOW_PERFORMANCE_THRESHOLD,
        row['attendance_rate'] if row['attendance_rate'] is not None else LOW_ATTENDANCE_THRESHOLD,
    ))

    enrolled_counts = {
        row['offering'].id: row['enrolled_count'] for row in offering_rows
    }

    return {
        'offerings': offerings,
        'offering_rows': offering_rows,
        'enrollment_count': len(enrollments),
        'student_count': len(student_ids),
        'average': _mean(all_scores),
        'scored_enrollments': len(all_scores),
        'attendance_overall': attendance_overall,
        'grade_distribution': score_band_distribution(all_scores),
        'course_performance': {
            'labels': [row['offering'].course.code for row in offering_rows],
            'values': [row['average'] for row in offering_rows],
        },
        'assessment_performance': assessment_performance_for_offerings(
            offerings, institution, enrolled_counts=enrolled_counts
        ),
        'attendance_trend': attendance_trend_for_offerings(offerings, institution),
        'attention_rows': attention_rows,
        'attention_count': len(attention_rows),
        'low_performance_count': len(low_performance_ids),
        'low_attendance_count': len(low_attendance_ids - low_performance_ids),
    }


def adviser_analytics(adviser, institution):
    assignments = list(
        AdviserAssignment.objects.filter(
            institution=institution,
            adviser=adviser,
            is_active=True,
        ).select_related(
            'student'
        ).order_by('student__last_name', 'student__first_name')
    )
    student_ids = [assignment.student_id for assignment in assignments]

    enrollments = list(
        Enrollment.objects.filter(
            institution=institution,
            student_id__in=student_ids,
            status=Enrollment.EnrollmentStatus.ACTIVE,
        ).select_related('student', 'course_offering__course', 'course_offering__semester')
    ) if student_ids else []

    performance_map = assessment_performance_map(institution, student_ids)
    _, attendance_overall = attendance_map(institution, student_ids)
    student_attendance, _ = attendance_by_student(institution, student_ids)

    by_student = {}
    for enrollment in enrollments:
        score = enrollment_performance(enrollment, performance_map)
        if score is not None:
            by_student.setdefault(enrollment.student_id, []).append(score)

    predictions = {}
    if student_ids:
        for prediction in RiskPrediction.objects.filter(
            institution=institution,
            student_id__in=student_ids,
            is_current=True,
        ).select_related('student'):
            predictions[prediction.student_id] = prediction

    student_rows = []
    for assignment in assignments:
        student = assignment.student
        prediction = predictions.get(student.id)
        student_rows.append({
            'assignment': assignment,
            'student': student,
            'average': _mean(by_student.get(student.id, [])),
            'attendance_rate': student_attendance.get(student.id),
            'prediction': prediction,
        })

    risk_counts = {
        RiskLevel.HIGH: 0,
        RiskLevel.MEDIUM: 0,
        RiskLevel.LOW: 0,
        'NONE': 0,
    }
    for prediction in predictions.values():
        risk_counts[prediction.risk_level] = risk_counts.get(
            prediction.risk_level, 0
        ) + 1
    risk_counts['NONE'] = len(student_ids) - len(predictions)

    at_risk = [
        row for row in student_rows
        if row['prediction'] and row['prediction'].risk_level in (
            RiskLevel.HIGH, RiskLevel.MEDIUM
        )
    ]
    at_risk.sort(
        key=lambda row: (
            0 if row['prediction'].risk_level == RiskLevel.HIGH else 1,
            -row['prediction'].risk_score,
        )
    )

    interventions = (
        AcademicIntervention.objects.filter(
            institution=institution,
            adviser=adviser,
        ).select_related('student').order_by('-date_conducted')
    )
    follow_ups = (
        AcademicIntervention.objects.filter(
            institution=institution,
            adviser=adviser,
            status__in=[InterventionStatus.OPEN, InterventionStatus.IN_PROGRESS],
        ).select_related('student').order_by('follow_up_date')[:8]
    )

    return {
        'assignments': assignments,
        'student_rows': student_rows,
        'student_ids': student_ids,
        'student_count': len(student_ids),
        'performance_trend': performance_by_semester(student_ids, institution),
        'attendance_trend': attendance_by_semester(student_ids, institution),
        'attendance_overall': attendance_overall,
        'risk_counts': risk_counts,
        'risk_levels': ['High risk', 'Moderate risk', 'Low risk', 'No estimate'],
        'risk_level_counts': [
            risk_counts[RiskLevel.HIGH],
            risk_counts[RiskLevel.MEDIUM],
            risk_counts[RiskLevel.LOW],
            risk_counts['NONE'],
        ],
        'high_risk_count': risk_counts[RiskLevel.HIGH],
        'medium_risk_count': risk_counts[RiskLevel.MEDIUM],
        'low_risk_count': risk_counts[RiskLevel.LOW],
        'at_risk_students': at_risk,
        'interventions': interventions,
        'follow_ups': follow_ups,
        'open_intervention_count': interventions.filter(
            status__in=[InterventionStatus.OPEN, InterventionStatus.IN_PROGRESS]
        ).count(),
    }


def department_performance(
    institution, performance_map=None, offering_attendance=None
):
    """
    Average recorded performance per department.

    ``performance_map`` may be shared with the caller to avoid recomputing the
    same institution-wide assessment scan; it is built on demand otherwise.
    ``offering_attendance`` optionally maps ``offering_id`` to the
    ``{'attended': n, 'total': n}`` counts from ``attendance_counts_by_offering``
    so department-level attendance is rolled up from the same grouped query
    instead of scanning attendance records again.
    """
    departments = list(
        Department.objects.filter(institution=institution)
        .select_related('faculty')
        .order_by('name')
    )
    enrollments = Enrollment.objects.filter(
        institution=institution,
    ).select_related(
        # Faculty is needed for every department row; without it each unique
        # department triggers another query.
        'course_offering__course__department__faculty',
    )
    if performance_map is None:
        performance_map = assessment_performance_map(institution)

    buckets = {}
    attendance_buckets = {}
    for enrollment in enrollments:
        score = enrollment_performance(enrollment, performance_map)
        department = enrollment.course_offering.course.department
        bucket = buckets.setdefault(department.id, {
            'name': department.name,
            'faculty': department.faculty.name,
            'scores': [],
            'students': set(),
        })
        if score is not None:
            bucket['scores'].append(score)
            bucket['students'].add(enrollment.student_id)
        if offering_attendance is not None:
            counts = offering_attendance.get(enrollment.course_offering_id)
            if counts:
                attendance_bucket = attendance_buckets.setdefault(
                    department.id, {'attended': 0, 'total': 0}
                )
                attendance_bucket['attended'] += counts['attended']
                attendance_bucket['total'] += counts['total']

    rows = []
    for department in departments:
        bucket = buckets.get(department.id)
        attendance_bucket = attendance_buckets.get(department.id)
        attendance_rate = None
        if attendance_bucket and attendance_bucket['total']:
            attendance_rate = round(
                (attendance_bucket['attended'] / attendance_bucket['total'])
                * 100,
                1,
            )
        rows.append({
            'department': department,
            'average': _mean(bucket['scores']) if bucket else None,
            'student_count': len(bucket['students']) if bucket else 0,
            'record_count': len(bucket['scores']) if bucket else 0,
            'attendance_rate': attendance_rate,
            'attendance_entries': (
                attendance_bucket['total'] if attendance_bucket else 0
            ),
        })

    scored = [row for row in rows if row['average'] is not None]
    return {'rows': rows, 'scored': scored}


def institution_analytics(institution, course_performance_limit=12):
    memberships = institution.memberships.filter(status='APPROVED')
    role_counts = {
        row['role']: row['count']
        for row in memberships.values('role').annotate(count=Count('id'))
    }

    enrollments = Enrollment.objects.filter(institution=institution)
    active_enrollments = enrollments.filter(
        status=Enrollment.EnrollmentStatus.ACTIVE
    )
    student_ids = list(
        memberships.filter(role='STUDENT').values_list('user_id', flat=True)
    )

    offerings = CourseOffering.objects.filter(institution=institution)
    active_offerings = offerings.filter(is_active=True)

    # One institution-wide assessment scan feeds the overall scores, the
    # department table, the class table and the course chart below; each
    # previously ran its own copy of this query.
    performance_map = assessment_performance_map(institution)

    # Single pass over the student enrolments: overall scores, per-class
    # buckets for the class table, and per-student aggregates for the
    # attention list are accumulated together (no extra query).
    student_users = {}
    class_buckets = {}
    student_attention = {}
    all_scores = []
    for enrollment in (
        enrollments.filter(student_id__in=student_ids)
        .select_related('student')
    ):
        score = enrollment_performance(enrollment, performance_map)
        if score is not None:
            all_scores.append(score)
            class_bucket = class_buckets.setdefault(
                enrollment.course_offering_id,
                {'scores': [], 'students': set(), 'low_students': set()},
            )
            class_bucket['scores'].append(score)
            class_bucket['students'].add(enrollment.student_id)
            if score < LOW_PERFORMANCE_THRESHOLD:
                class_bucket['low_students'].add(enrollment.student_id)
            attention = student_attention.setdefault(
                enrollment.student_id,
                {'below': 0, 'worst': score},
            )
            if score < LOW_PERFORMANCE_THRESHOLD:
                attention['below'] += 1
            attention['worst'] = min(attention['worst'], score)
        student_users[enrollment.student_id] = enrollment.student

    current_session = AcademicSession.objects.filter(
        institution=institution, is_current=True
    ).first()
    current_semester = Semester.objects.filter(
        institution=institution, is_current=True
    ).first()

    # Class-level table: one row per active offering. Averages are published
    # only above MIN_CLASS_GROUP_SIZE recorded results; attendance comes from
    # one grouped query and is an aggregate count.
    offering_list = list(
        active_offerings.select_related(
            'course__department__faculty',
            'semester__session',
            'instructor',
        ).order_by('course__code')[:50]
    )
    offering_attendance = attendance_counts_by_offering(
        institution, [offering.id for offering in offering_list]
    )
    class_rows = []
    for offering in offering_list:
        bucket = class_buckets.get(offering.id)
        scored = len(bucket['scores']) if bucket else 0
        class_rows.append({
            'offering': offering,
            'enrolled_count': len(bucket['students']) if bucket else 0,
            'record_count': scored,
            'average': (
                _mean(bucket['scores'])
                if scored >= MIN_CLASS_GROUP_SIZE else None
            ),
            'below_threshold_count': (
                len(bucket['low_students']) if bucket else 0
            ),
            'attendance': offering_attendance.get(offering.id, {}).get('rate'),
            'attendance_entries': offering_attendance.get(
                offering.id, {}
            ).get('total', 0),
            'suppressed': scored < MIN_CLASS_GROUP_SIZE,
        })

    attention_rows = []
    for student_id, info in student_attention.items():
        if info['below'] > 0:
            attention_rows.append({
                'student': student_users.get(student_id),
                'below_count': info['below'],
                'worst_score': info['worst'],
            })
    attention_rows.sort(key=lambda row: row['worst_score'])

    return {
        'role_counts': role_counts,
        'student_count': role_counts.get('STUDENT', 0),
        'instructor_count': role_counts.get('INSTRUCTOR', 0),
        'adviser_count': role_counts.get('ADVISER', 0),
        'registrar_count': role_counts.get('REGISTRAR', 0),
        'admin_count': role_counts.get('INSTITUTION_ADMIN', 0),
        'department_count': Department.objects.filter(institution=institution).count(),
        'programme_count': Programme.objects.filter(institution=institution).count(),
        'course_count': Course.objects.filter(institution=institution).count(),
        'offering_count': active_offerings.count(),
        'enrollment_count': active_enrollments.count(),
        'average': _mean(all_scores),
        'scored_enrollments': len(all_scores),
        'grade_distribution': score_band_distribution(all_scores),
        'performance_trend': performance_by_semester(student_ids, institution),
        'attendance_trend': attendance_by_semester(student_ids, institution),
        'departments': department_performance(
            institution,
            performance_map=performance_map,
            offering_attendance=offering_attendance,
        ),
        'class_rows': class_rows,
        'class_count': active_offerings.count(),
        'attention_rows': attention_rows[:10],
        'attention_count': len(attention_rows),
        'current_session': current_session,
        'current_semester': current_semester,
        'course_performance': course_performance_for_institution(
            institution,
            limit=course_performance_limit,
            performance_map=performance_map,
        ),
    }


def course_performance_for_institution(institution, limit=12, performance_map=None):
    """
    Average recorded performance per active offering.

    ``performance_map`` may be shared with the caller to avoid recomputing an
    institution-wide assessment scan; it is built on demand otherwise.
    """
    offerings = list(
        CourseOffering.objects.filter(
            institution=institution,
            is_active=True,
        ).select_related('course', 'semester').order_by('course__code')[:limit]
    )
    if not offerings:
        return {'labels': [], 'values': [], 'meta': []}

    enrollments = Enrollment.objects.filter(
        institution=institution,
        course_offering_id__in=[offering.id for offering in offerings],
        status=Enrollment.EnrollmentStatus.ACTIVE,
    )
    if performance_map is None:
        student_ids = list(enrollments.values_list('student_id', flat=True).distinct())
        performance_map = assessment_performance_map(institution, student_ids)

    buckets = {}
    for enrollment in enrollments:
        score = enrollment_performance(enrollment, performance_map)
        if score is None:
            continue
        buckets.setdefault(enrollment.course_offering_id, []).append(score)

    values = []
    meta = []
    for offering in offerings:
        scores = buckets.get(offering.id, [])
        scored = len(scores)
        # Same small-class suppression as the class table: a single offering's
        # average is published only above MIN_CLASS_GROUP_SIZE results.
        if scored >= MIN_CLASS_GROUP_SIZE:
            values.append(_mean(scores))
            meta.append(f'{scored} recorded result(s)')
        else:
            plural = 's' if scored != 1 else ''
            values.append(None)
            meta.append(
                f'Insufficient data — {scored} recorded result{plural} '
                f'(minimum {MIN_CLASS_GROUP_SIZE})'
            )

    return {
        'labels': [offering.course.code for offering in offerings],
        'values': values,
        'meta': meta,
    }


def student_progress(student, institution):
    enrollments = list(
        Enrollment.objects.filter(
            student=student,
            institution=institution,
        ).select_related(
            'course_offering__course',
            'course_offering__semester__session',
        ).order_by(
            '-course_offering__semester__start_date',
            'course_offering__course__code',
        )
    )
    performance_map = assessment_performance_map(institution, [student.id])

    semesters = {}
    total_credits = 0
    total_points = 0.0
    for enrollment in enrollments:
        semester = enrollment.course_offering.semester
        bucket = semesters.setdefault(semester.id, {
            'semester': semester,
            'rows': [],
            'credits': 0,
            'points': 0.0,
            'score_values': [],
        })
        performance = enrollment_performance(enrollment, performance_map)
        bucket['rows'].append({
            'enrollment': enrollment,
            'course': enrollment.course_offering.course,
            'performance': performance,
            'final_score': (
                float(enrollment.final_score)
                if enrollment.final_score is not None else None
            ),
            'final_grade': enrollment.final_grade,
            'grade_point': (
                float(enrollment.grade_point)
                if enrollment.grade_point is not None else None
            ),
        })
        if performance is not None:
            bucket['score_values'].append(performance)
        if enrollment.grade_point is not None:
            credits = enrollment.course_offering.course.credit_units
            bucket['credits'] += credits
            bucket['points'] += float(enrollment.grade_point) * credits
            total_credits += credits
            total_points += float(enrollment.grade_point) * credits

    semester_rows = []
    for bucket in sorted(
        semesters.values(),
        key=lambda item: (item['semester'].start_date, item['semester'].id),
        reverse=True,
    ):
        semester_rows.append({
            'semester': bucket['semester'],
            'rows': bucket['rows'],
            'average': _mean(bucket['score_values']),
            'gpa': (
                round(bucket['points'] / bucket['credits'], 2)
                if bucket['credits'] else None
            ),
            'credits': bucket['credits'],
        })

    cumulative_gpa = (
        round(total_points / total_credits, 2) if total_credits else None
    )

    return {
        'semesters': semester_rows,
        'cumulative_gpa': cumulative_gpa,
        'completed_credits': total_credits,
        'performance_trend': performance_by_semester([student.id], institution),
        'attendance_trend': attendance_by_semester([student.id], institution),
        'course_count': len(enrollments),
    }
