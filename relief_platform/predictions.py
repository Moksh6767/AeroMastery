"""Shared training/inference features. Synthetic labels do not validate real safety."""

import json
import random
from pathlib import Path
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.model_selection import train_test_split

FEATURES = ["hours", "faults", "days_since_service"]


def features(record):
    return [float(record[k]) for k in FEATURES]


def train(directory, seed=42):
    rng = random.Random(seed)
    rows = []
    labels = []
    for _ in range(1600):
        row = {
            "hours": rng.uniform(0, 400),
            "faults": rng.randrange(6),
            "days_since_service": rng.randrange(60),
        }
        probability = min(
            0.85,
            0.03
            + row["hours"] * 0.0007
            + row["faults"] * 0.065
            + row["days_since_service"] * 0.004,
        )
        rows.append(features(row))
        labels.append(int(rng.random() < probability))
    x_train, x_test, y_train, y_test = train_test_split(
        rows, labels, test_size=0.25, random_state=seed, stratify=labels
    )
    model = RandomForestClassifier(
        n_estimators=80, max_depth=6, min_samples_leaf=12, random_state=seed, n_jobs=1
    )
    model.fit(x_train, y_train)
    probabilities = model.predict_proba(x_test)[:, 1]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, directory / "serviceability.joblib")
    report = {
        "synthetic": True,
        "seed": seed,
        "features": FEATURES,
        "training_rows": len(x_train),
        "test_rows": len(x_test),
        "roc_auc": roc_auc_score(y_test, probabilities),
        "brier_score": brier_score_loss(y_test, probabilities),
        "weather_method": "declared synthetic scenario probability; no trained weather model",
    }
    (directory / "prediction_metrics.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report


def predict(world, directory):
    path = Path(directory) / "serviceability.joblib"
    model = joblib.load(path) if path.exists() else None
    history = {r["aircraft_id"]: r for r in world["maintenance"]}
    predictions = []
    for aircraft in world["aircraft"]:
        risk = (
            float(model.predict_proba([features(history[aircraft["id"]])])[0, 1])
            if model
            else 1 - aircraft["serviceability"]
        )
        aircraft["serviceability"] = round(1 - risk, 5)
        predictions.append(
            {
                "aircraft_id": aircraft["id"],
                "unserviceability_probability": risk,
                "model": (
                    "synthetic-trained random forest"
                    if model
                    else "scenario prior (model not trained)"
                ),
                "synthetic": True,
            }
        )
    return {
        "aircraft": predictions,
        "weather": [
            {
                "base_id": b["id"],
                "risk": b["weather_failure"],
                "window_minutes": [0, 480],
                "synthetic": True,
            }
            for b in world["bases"]
        ],
    }
