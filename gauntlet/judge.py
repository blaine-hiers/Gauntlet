import json
import re
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


def judge_output(
    output_text: str, judge_spec: dict, judge_model: str, timeout_s: int = 300
) -> dict:
    answer_key_block = (
        f"Answer key (ground truth):\n{judge_spec['answer_key']}\n\n"
        if judge_spec.get("answer_key")
        else ""
    )
    prompt = JUDGE_PROMPT.format(
        rubric=judge_spec["rubric"], answer_key_block=answer_key_block, output=output_text
    )
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
        return {"score": None, "reasoning": "judge timed out"}
    try:
        result_text = json.loads(proc.stdout).get("result", "")
    except json.JSONDecodeError:
        result_text = proc.stdout
    m = re.search(r"\{.*\}", result_text, re.DOTALL)
    if not m:
        return {"score": None, "reasoning": f"unparseable judge reply: {result_text[:200]}"}
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"score": None, "reasoning": f"unparseable judge reply: {result_text[:200]}"}
    score = parsed.get("score")
    reasoning = parsed.get("reasoning", "")
    if not (isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 10):
        return {"score": None, "reasoning": f"invalid judge score: {score!r}"}
    return {"score": score, "reasoning": reasoning}
