"""Frozen encoders plus an ordinal classifier and class-specific fine heads."""
import os
import threading
import numpy as np
from .storage import DATA

LOCK = threading.RLock()
COARSE_WEIGHT = 0.85
FINE_WEIGHT = 0.15


def coarse_class(score):
    # 70..79 belong to the 70-point stage; 100 is a separate stage.
    return min(9, max(0, int(score) // 10 - 1))


def weighted_error(actual, predicted):
    return COARSE_WEIGHT * abs(coarse_class(actual) - coarse_class(predicted)) / 9 + FINE_WEIGHT * abs(actual - predicted) / 90


def head_path(model_id):
    path = DATA / "profiles" / model_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "head.npz"


def fit_parameters(vectors, scores, label_ids=(), dataset_signature=""):
    x = np.asarray(vectors, dtype=np.float64)
    y = np.asarray([coarse_class(s) for s in scores])
    mean, scale = x.mean(0), np.maximum(x.std(0), 0.03)
    z = np.column_stack([np.clip((x - mean) / scale, -5, 5), np.ones(len(x))])
    z[:, :-1] /= np.sqrt(x.shape[1])
    weights = np.zeros((z.shape[1], 10))
    fine = np.zeros_like(weights)
    target = np.eye(10)[y]
    distances = np.abs(np.arange(10)[None, :] - y[:, None]) / 9
    fine_target = (np.asarray(scores) - (y + 1) * 10) / 9
    for _ in range(400):
        logits = z @ weights
        p = np.exp(logits - logits.max(axis=1, keepdims=True))
        p /= p.sum(axis=1, keepdims=True)
        ordinal_gradient = p * (distances - (p * distances).sum(axis=1, keepdims=True))
        gradient = COARSE_WEIGHT * (p - target + ordinal_gradient)
        weights -= 0.3 * (z.T @ gradient / len(x) + 0.005 * weights)
        residual = (z @ fine)[np.arange(len(x)), y] - fine_target
        fine_gradient = np.zeros_like(p)
        fine_gradient[np.arange(len(x)), y] = 2 * FINE_WEIGHT * residual
        fine -= 0.3 * (z.T @ fine_gradient / len(x) + 0.005 * fine)
    return dict(mean=mean, scale=scale, weights=weights, fine=fine, count=len(x),
                label_ids=np.array(label_ids), classes=np.unique(y), dataset_signature=dataset_signature)


def fit_head(model_id, vectors, scores, label_ids, dataset_signature=""):
    parameters = fit_parameters(vectors, scores, label_ids, dataset_signature)
    path = head_path(model_id)
    with LOCK:
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            np.savez_compressed(stream, **parameters)
        os.replace(temporary, path)
    return {"count": len(vectors), "classes": len(parameters["classes"]), "coarse_weight": COARSE_WEIGHT, "fine_weight": FINE_WEIGHT}


def head_status(model_id):
    path = head_path(model_id)
    with LOCK:
        if not path.exists():
            return {"count": 0, "classes": 0, "dataset_signature": ""}
        with np.load(path, allow_pickle=False) as state:
            return {"count": int(state["count"]), "classes": len(state["classes"]),
                    "dataset_signature": str(state["dataset_signature"].item()) if "dataset_signature" in state else ""}


def predict_parameters(state, vector):
    if len(vector) != len(state["mean"]):
        raise ValueError("Merkmalsdimension passt nicht zum Lernstand. Bitte neu trainieren.")
    z = np.r_[np.clip((vector - state["mean"]) / state["scale"], -5, 5) / np.sqrt(len(vector)), 1.0]
    logits = z @ state["weights"]
    probabilities = np.exp(logits - logits.max())
    probabilities /= probabilities.sum()
    selected = int(probabilities.argmax())
    fine = int(round(np.clip((z @ state["fine"])[selected], 0, 1) * 9))
    score = min(100, (selected + 1) * 10 + fine)
    count = int(state["count"])
    entropy = float(-np.sum(probabilities * np.log(probabilities + 1e-12)) / np.log(10))
    uncertainty = max(entropy, max(0, 1 - count / 30))
    return {"score": score, "coarse": (selected + 1) * 10, "uncertainty": round(uncertainty, 4),
            "count": count, "probabilities": probabilities.round(5).tolist()}


def predict_head(model_id, vector):
    path = head_path(model_id)
    with LOCK:
        if not path.exists():
            return None
        with np.load(path, allow_pickle=False) as state:
            return predict_parameters(state, vector)
