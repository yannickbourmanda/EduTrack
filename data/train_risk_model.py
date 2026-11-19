"""
EduTrack Machine Learning Early Warning Model Training & Evaluation Script
Trains and compares Logistic Regression, Decision Tree, and Random Forest models
for predicting students at risk of academic difficulty.

IMPORTANT - DATA PROVENANCE AND LIMITATIONS
-------------------------------------------
The dataset produced by ``generate_academic_training_data`` is SYNTHETIC
simulation data, generated from seeded numpy distributions. It is NOT
institutional data. The risk label is produced by a separate causal process
(a latent risk factor plus outcome noise that is independent of the observed
feature noise), so the label is not a deterministic function of the eight
observed features - but the resulting metrics still characterise the
simulation, not real academic prediction.

Before any production reliance on the model:
1. Retrain on de-identified institutional records.
2. Use a time-based (not random) split so the model never sees the semester
   it predicts.
3. Report per-class precision/recall, calibration and subgroup performance.

Saves (unless run with ``--dry-run``, which writes nothing):
- ml_artifacts/academic_risk_rf_v1.joblib
- ml_artifacts/model_metrics_v1.json
- data/academic_training_dataset.csv
"""

import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

np.random.seed(42)

N_SPLITS = 5
# Selection rule: weighted F1 under cross-validation on the training split.
# Weighted F1 balances the three risk classes while respecting class imbalance;
# the winning candidate is then re-fitted on the full training split and
# measured once on the held-out test split.
SELECTION_SCORING = 'f1_weighted'


def generate_academic_training_data(n_samples=1200):
    """
    Constructs a synthetic simulation dataset for developing and demonstrating
    the risk pipeline. Observed features are noisy measurements of a latent
    risk factor; the label comes from the latent factor plus outcome noise
    that is independent of the feature noise, so no deterministic formula ties
    the label to the observed columns.

    Features:
    - attendance_rate: Attendance percentage (0 - 100)
    - assessment_avg: Average assignment/test score (0 - 100)
    - quiz_avg: Average quiz score (0 - 100)
    - midterm_score: Midterm exam score (0 - 100)
    - participation_score: Participation on 1-5 scale
    - failed_assessments_count: Number of failed continuous assessments (0 - 6)
    - prior_gpa: Prior cumulative GPA on 5.0 Nigerian scale (0.0 - 5.0)
    - credit_load: Enrolled credit units (12 - 24)
    """
    latent_risk = np.random.normal(0.0, 1.0, n_samples)  # hidden driver

    attendance = np.clip(78 - latent_risk * 16 + np.random.normal(0, 8, n_samples), 10, 100)
    assessment = np.clip(62 - latent_risk * 18 + np.random.normal(0, 9, n_samples), 5, 100)
    quiz = np.clip(assessment + np.random.normal(0, 10, n_samples), 0, 100)
    midterm = np.clip(
        0.55 * assessment + 0.25 * attendance + np.random.normal(0, 9, n_samples), 0, 100
    )
    participation = np.clip(
        np.round((attendance / 25) + np.random.normal(0, 0.8, n_samples)), 1, 5
    )

    failed_count = np.where(
        assessment < 45,
        np.random.randint(2, 6, n_samples),
        np.where(assessment < 60, np.random.randint(0, 3, n_samples), 0),
    )

    prior_gpa = np.clip(3.2 - latent_risk * 0.6 + np.random.normal(0, 0.6, n_samples), 1.0, 5.0)
    credit_load = np.random.choice(
        [15, 18, 20, 21, 24], n_samples, p=[0.2, 0.4, 0.2, 0.1, 0.1]
    )

    # Outcome process: the latent factor plus noise independent of the feature
    # noise above. Binning is quantile-free and fixed so class boundaries are
    # reproducible across runs.
    outcome = latent_risk + np.random.normal(0.0, 0.5, n_samples)
    risk_label = np.zeros(n_samples, dtype=int)
    risk_label[outcome >= 0.0] = 1   # Medium risk
    risk_label[outcome >= 1.0] = 2   # High risk

    df = pd.DataFrame({
        'attendance_rate': attendance,
        'assessment_avg': assessment,
        'quiz_avg': quiz,
        'midterm_score': midterm,
        'participation_score': participation,
        'failed_assessments_count': failed_count,
        'prior_gpa': prior_gpa,
        'credit_load': credit_load,
        'risk_level': risk_label
    })

    return df


def _feature_importances(model, feature_names):
    """Importances for tree ensembles/bare trees; empty for pipelines."""
    estimator = model
    if isinstance(model, Pipeline):
        estimator = model.named_steps.get('clf')
    if hasattr(estimator, 'feature_importances_'):
        return {
            name: round(float(imp), 4)
            for name, imp in zip(feature_names, estimator.feature_importances_)
        }
    return {}


def main():
    dry_run = '--dry-run' in sys.argv

    print("[ML Pipeline] Generating synthetic academic training dataset...")
    df = generate_academic_training_data(1500)
    print(
        f"[ML Pipeline] Dataset generated with {len(df)} records. "
        f"Risk distribution:\n{df['risk_level'].value_counts()}"
    )

    X = df.drop(columns=['risk_level'])
    y = df['risk_level']

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )

    models = {
        'Logistic Regression': Pipeline([
            ('scaler', StandardScaler()),
            ('clf', LogisticRegression(max_iter=500, class_weight='balanced', random_state=42))
        ]),
        'Decision Tree': DecisionTreeClassifier(max_depth=5, min_samples_split=10, random_state=42),
        'Random Forest': RandomForestClassifier(
            n_estimators=100, max_depth=6, min_samples_split=8,
            class_weight='balanced', random_state=42
        ),
    }

    # ------------------------------------------------------------------
    # Model selection: cross-validated on the TRAINING split only, so the
    # held-out test split stays untouched by the selection decision.
    # ------------------------------------------------------------------
    cv = StratifiedKFold(n_splits=N_SPLITS, shuffle=True, random_state=42)
    selection_scores = {}
    print("\n--- Model selection (cross-validated on the training split) ---")
    for name, model in models.items():
        cv_scores = cross_val_score(
            model, X_train, y_train, cv=cv, scoring=SELECTION_SCORING, n_jobs=1
        )
        selection_scores[name] = {
            'mean': round(float(np.mean(cv_scores)), 4),
            'std': round(float(np.std(cv_scores)), 4),
        }
        print(
            f"[{name}] CV {SELECTION_SCORING}: "
            f"{selection_scores[name]['mean']:.4f} +/- {selection_scores[name]['std']:.4f}"
        )

    best_model_name = max(selection_scores, key=lambda name: selection_scores[name]['mean'])
    print(f"\n[ML Pipeline] Selected by CV {SELECTION_SCORING}: {best_model_name}")

    # ------------------------------------------------------------------
    # Held-out evaluation of every candidate on the untouched test split.
    # ------------------------------------------------------------------
    results = {}
    print("\n--- Held-out evaluation (test split) ---")
    for name, model in models.items():
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)

        acc = accuracy_score(y_test, y_pred)
        prec = precision_score(y_test, y_pred, average='weighted', zero_division=0)
        rec = recall_score(y_test, y_pred, average='weighted', zero_division=0)
        f1 = f1_score(y_test, y_pred, average='weighted', zero_division=0)
        cm = confusion_matrix(y_test, y_pred).tolist()

        # High risk specific recall (Crucial for early warning to prevent missing at-risk students)
        high_risk_recall = recall_score(y_test == 2, y_pred == 2, zero_division=0)

        results[name] = {
            'accuracy': round(acc, 4),
            'precision': round(prec, 4),
            'recall': round(rec, 4),
            'f1_score': round(f1, 4),
            'high_risk_recall': round(high_risk_recall, 4),
            'confusion_matrix': cm
        }
        print(f"[{name}] Acc: {acc:.4f} | Rec: {rec:.4f} | F1: {f1:.4f} | High-Risk Recall: {high_risk_recall:.4f}")

    best_pipeline = models[best_model_name]

    if dry_run:
        print("\n[ML Pipeline] --dry-run: no artifacts written.")
        print(f"[ML Pipeline] Would serialise {best_model_name} to ml_artifacts/academic_risk_rf_v1.joblib")
        print(f"[ML Pipeline] Would write metrics to ml_artifacts/model_metrics_v1.json")
        return

    os.makedirs('data', exist_ok=True)
    os.makedirs('ml_artifacts', exist_ok=True)
    df.to_csv('data/academic_training_dataset.csv', index=False)
    print(f"\n[ML Pipeline] Synthetic dataset saved to data/academic_training_dataset.csv")

    # Save trained best model
    model_filepath = 'ml_artifacts/academic_risk_rf_v1.joblib'
    joblib.dump(best_pipeline, model_filepath)
    print(f"[ML Pipeline] Best model ({best_model_name}) serialized to {model_filepath}")

    # Save evaluation metrics
    metrics_filepath = 'ml_artifacts/model_metrics_v1.json'
    with open(metrics_filepath, 'w') as f:
        json.dump({
            'selected_model': best_model_name,
            'selection_rule': (
                f'Highest mean {SELECTION_SCORING} under {N_SPLITS}-fold '
                'stratified cross-validation on the training split.'
            ),
            'selection_scores': selection_scores,
            'features': list(X.columns),
            'target_classes': ['Low Risk', 'Moderate Risk', 'High Risk'],
            'models_evaluation': results,
            'feature_importances': _feature_importances(best_pipeline, list(X.columns)),
            'provenance': {
                'dataset': (
                    'Synthetic simulation data generated by data/train_risk_model.py '
                    '(seeded numpy distributions); NOT institutional records. The risk '
                    'label comes from a latent factor plus outcome noise independent of '
                    'the observed feature noise.'
                ),
                'validation': (
                    f'{N_SPLITS}-fold stratified cross-validation for selection on the '
                    'training split; single held-out test split (25%) for the reported '
                    'metrics. No time-based split - the dataset is not sequential.'
                ),
                'calibration': (
                    'Probability outputs are the classifier\'s predict_proba values; '
                    'no calibration procedure is applied.'
                ),
                'real_world_validation': (
                    'None. Retrain on de-identified institutional records with a '
                    'time-based split and report per-class, calibration and subgroup '
                    'metrics before production reliance.'
                ),
                'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            },
            'classification_report': classification_report(
                y_test, best_pipeline.predict(X_test),
                target_names=['Low Risk', 'Moderate Risk', 'High Risk'],
                zero_division=0,
            ),
        }, f, indent=2)
    print(f"[ML Pipeline] Metrics and metadata saved to {metrics_filepath}")


if __name__ == '__main__':
    main()
