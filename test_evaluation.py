from uuid import uuid4

import pytest

from evaluate_scores import evaluate, interval
from inference import MODEL_ID


def pair(left, right, score, split="test"):
    return {
        "left_subject": left,
        "right_subject": right,
        "left_call": str(uuid4()),
        "right_call": str(uuid4()),
        "score": str(score),
        "model_id": MODEL_ID,
        "split": split,
        "label": "genuine" if left == right else "impostor",
    }


def test_error_counts_and_uncertainty():
    a, b = str(uuid4()), str(uuid4())
    rows = [pair(a, a, 0.9), pair(b, b, 0.5), pair(a, b, 0.85), pair(a, b, 0.2)]
    result = evaluate(rows, 0.8)
    assert result["false_accept_rate"] == result["false_reject_rate"] == 0.5
    assert result["false_accepts"] == result["false_rejects"] == 1
    assert interval(0, 10)[1] > 0.25
    assert result["policy_automatically_approved"] is False


def test_no_speaker_leakage_or_duplicate_pair():
    a, b = str(uuid4()), str(uuid4())
    rows = [pair(a, a, 0.9), pair(a, b, 0.1), pair(a, a, 0.8, "tuning")]
    with pytest.raises(ValueError, match="leakage"):
        evaluate(rows, 0.8)
    rows = [pair(a, a, 0.9), pair(a, b, 0.1)]
    rows.append(rows[1])
    with pytest.raises(ValueError, match="duplicate"):
        evaluate(rows, 0.8)
