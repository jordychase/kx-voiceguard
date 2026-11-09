"""Evaluate held-out, consented pair scores without loading audio or changing server policy."""

import argparse
import csv
import json
import math
from collections import defaultdict
from uuid import UUID

from inference import MODEL_ID


def interval(errors, trials):
    """Wilson 95% interval; correlated pairs still require speaker-level resampling."""
    if trials == 0:
        return None
    z = 1.95996398454
    p = errors / trials
    denominator = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def evaluate(rows, threshold):
    if not math.isfinite(threshold) or not -1 <= threshold <= 1:
        raise ValueError("threshold must be finite and between -1 and 1")
    speakers, seen_pairs = defaultdict(set), set()
    genuine, impostor, misses, accepts = 0, 0, 0, 0
    for row in rows:
        if row["model_id"] != MODEL_ID or row["split"] not in {"tuning", "test"}:
            raise ValueError("wrong model or invalid split")
        left, right = str(UUID(row["left_subject"])), str(UUID(row["right_subject"]))
        call1, call2 = str(UUID(row["left_call"])), str(UUID(row["right_call"]))
        if call1 == call2:
            raise ValueError("pairs must use separate calls")
        pair = tuple(sorted((call1, call2)))
        if pair in seen_pairs:
            raise ValueError("duplicate/reversed call pair")
        seen_pairs.add(pair)
        speakers[row["split"]].update((left, right))
        label = row["label"]
        if label not in {"genuine", "impostor"} or (label == "genuine") != (left == right):
            raise ValueError("label disagrees with subject IDs")
        score = float(row["score"])
        if not math.isfinite(score) or not -1 <= score <= 1:
            raise ValueError("invalid score")
        if row["split"] != "test":
            continue
        if label == "genuine":
            genuine += 1
            misses += score < threshold
        else:
            impostor += 1
            accepts += score >= threshold
    if speakers["tuning"] & speakers["test"]:
        raise ValueError("speaker leakage between tuning and test")
    if not genuine or not impostor:
        raise ValueError("held-out test requires both genuine and impostor pairs")
    return {
        "model_id": MODEL_ID,
        "threshold": threshold,
        "genuine_pairs": genuine,
        "impostor_pairs": impostor,
        "false_accepts": accepts,
        "false_rejects": misses,
        "false_accept_rate": accepts / impostor,
        "false_reject_rate": misses / genuine,
        "false_accept_95pct_wilson": interval(accepts, impostor),
        "false_reject_95pct_wilson": interval(misses, genuine),
        "test_speakers": len(speakers["test"]),
        "policy_automatically_approved": False,
        "limitations": "Pair intervals assume independent trials. Shared speakers/calls require clustered resampling; 1:N and spoofing need separate evaluation.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scores_csv")
    parser.add_argument("--threshold", type=float, required=True)
    args = parser.parse_args()
    with open(args.scores_csv, newline="") as file:
        result = evaluate(csv.DictReader(file), args.threshold)
    print(json.dumps(result, indent=2))
