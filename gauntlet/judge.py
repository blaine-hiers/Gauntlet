import json
import re
import statistics
import subprocess

from gauntlet.runner import find_claude

JUDGE_PROMPT = """You are grading an AI assistant's output against a rubric. Grade the outcome \
quality, not obedience: an output that follows instructions but is unhelpful or wrong scores low.

Rubric:
{rubric}

{answer_key_block}Assistant output to grade:
---
{output}
---

Respond with ONLY a JSON object: {{"score": <integer 0-10>, "reasoning": "<one short paragraph>"}}"""


def _call_once(prompt: str, judge_model: str, timeout_s: int) -> dict:
    """One subprocess call to the judge, parsed. `score` is None on any
    failure (timeout, non-JSON CLI payload, unparseable reply, out-of-range
    score). `retryable` tells the caller whether that failure is worth a
    retry: only "the CLI ran and returned text we could not parse" is — a
    timeout already cost a full `timeout_s` once and retrying it would cost
    that again, and an in-range-but-invalid score is a real (if unusable)
    answer, not a parse failure."""
    try:
        proc = subprocess.run(
            [find_claude(), "-p", prompt, "--output-format", "json", "--model", judge_model],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {
            "score": None,
            "reasoning": "judge timed out",
            "cost_usd": None,
            "raw_reply": "",
            "retryable": False,
        }
    try:
        cli_payload = json.loads(proc.stdout)
        result_text = cli_payload.get("result", "")
        cost_usd = cli_payload.get("total_cost_usd")
    except json.JSONDecodeError:
        result_text = proc.stdout
        cost_usd = None
    m = re.search(r"\{.*\}", result_text, re.DOTALL)
    if not m:
        return {
            "score": None,
            "reasoning": f"unparseable judge reply: {result_text[:200]}",
            "cost_usd": cost_usd,
            "raw_reply": result_text,
            "retryable": True,
        }
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {
            "score": None,
            "reasoning": f"unparseable judge reply: {result_text[:200]}",
            "cost_usd": cost_usd,
            "raw_reply": result_text,
            "retryable": True,
        }
    score = parsed.get("score")
    reasoning = parsed.get("reasoning", "")
    if not (isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 10):
        return {
            "score": None,
            "reasoning": f"invalid judge score: {score!r}",
            "cost_usd": cost_usd,
            "raw_reply": result_text,
            "retryable": False,
        }
    return {
        "score": score,
        "reasoning": reasoning,
        "cost_usd": cost_usd,
        "raw_reply": result_text,
        "retryable": False,
    }


def judge_output(
    output_text: str,
    judge_spec: dict,
    judge_model: str,
    timeout_s: int = 300,
    samples: int = 1,
) -> dict:
    """Grades once (or `samples` times, taking the median score). An
    unparseable reply is retried once before being accepted as a failure.
    `degraded` is set whenever the returned verdict rests on fewer usable
    samples than were attempted, so the report can surface it instead of
    quietly folding it into the mean. `cost_usd` is the judge's own CLI
    spend, summed across every attempt — harness overhead, kept separate
    from the variant's measured cost. `raw_reply` is the last reply text,
    kept for audit."""
    answer_key_block = (
        f"Answer key (ground truth):\n{judge_spec['answer_key']}\n\n"
        if judge_spec.get("answer_key")
        else ""
    )
    prompt = JUDGE_PROMPT.format(
        rubric=judge_spec["rubric"], answer_key_block=answer_key_block, output=output_text
    )

    attempts = []
    for _ in range(max(1, samples)):
        r = _call_once(prompt, judge_model, timeout_s)
        if r["score"] is None and r["retryable"]:
            first_cost = r["cost_usd"] or 0
            r = _call_once(prompt, judge_model, timeout_s)  # retry once, unparseable output only
            # The unparseable call was still billed; the sample's cost is both calls.
            r["cost_usd"] = (r["cost_usd"] or 0) + first_cost
        attempts.append(r)

    total_cost = sum(a["cost_usd"] or 0 for a in attempts)
    scores = [a["score"] for a in attempts if a["score"] is not None]
    degraded = len(scores) < len(attempts)
    last = attempts[-1]
    if not scores:
        return {
            "score": None,
            "reasoning": last["reasoning"],
            "cost_usd": total_cost,
            "raw_reply": last["raw_reply"],
            "degraded": True,
        }
    return {
        "score": statistics.median(scores),
        "reasoning": last["reasoning"] if last["score"] is not None else attempts[0]["reasoning"],
        "cost_usd": total_cost,
        "raw_reply": last["raw_reply"],
        "degraded": degraded,
    }
