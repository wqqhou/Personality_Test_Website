import main
# --- metric helpers (replaces duplicated logic) ---
def compute_percentages_from_session(session_answers: dict) -> dict[str, float]:
    """Compute metric percentages using your METRIC_CONFIG and saved answers."""
    metric_scores = {m: 0.0 for m in main.METRIC_CONFIG}
    metric_counts = {m: 0   for m in main.METRIC_CONFIG}

    for metric, questions in main.METRIC_CONFIG.items():
        for q in questions:
            qid = str(q["question"])        # stored as string keys
            direction = q["direction"]      # "positive" or "negative"
            ans = session_answers.get(qid)
            if ans is None:
                continue
            score = (int(ans) - 1) / 6.0    # normalize 0..1 (7-point scale)
            if direction == "negative":
                score = 1.0 - score
            metric_scores[metric] += score
            metric_counts[metric] += 1

    percentages = {
        m: round((metric_scores[m] / metric_counts[m]) * 100, 2) 
        if metric_counts[m] else 0.0
        for m in metric_scores
    }
    return percentages

def top_n_metrics(percentages: dict[str, float], n: int = 5) -> list[tuple[str, float]]:
    return sorted(percentages.items(), key=lambda kv: kv[1], reverse=True)[:n]
