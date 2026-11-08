"""Answer-similarity check.

Because every student sees the options in a different shuffled order, two students choosing the
SAME wrong option (the same option text, not the same letter) is a strong sign of copying.
A pair is flagged when they share at least `min_shared` identical wrong answers and those make up
at least `ratio` of the smaller wrong-answer count.
"""
import json

import db
from services import exams


def report(exam_id, min_shared=3, ratio=0.6):
    correct = {q["id"]: q["correct_index"] for q in exams.questions_of(exam_id)}
    rows = db.query(
        "SELECT a.id, a.answers, u.name, u.roll FROM attempts a JOIN users u ON u.id = a.user_id "
        "WHERE a.exam_id=%s AND a.submitted_at IS NOT NULL", (exam_id,))
    wrong = []
    for r in rows:
        answers = json.loads(r["answers"] or "{}")
        w = {int(q): a for q, a in answers.items() if int(q) in correct and a != correct[int(q)]}
        wrong.append((r, w))

    pairs = []
    for i in range(len(wrong)):
        for j in range(i + 1, len(wrong)):
            (ra, wa), (rb, wb) = wrong[i], wrong[j]
            shared = sum(1 for q, a in wa.items() if wb.get(q) == a)
            smaller = min(len(wa), len(wb))
            if smaller and shared >= min_shared and shared / smaller >= ratio:
                pairs.append({"a_id": ra["id"], "a_name": ra["name"], "a_roll": ra["roll"],
                              "b_id": rb["id"], "b_name": rb["name"], "b_roll": rb["roll"],
                              "shared": shared, "wrong_a": len(wa), "wrong_b": len(wb),
                              "percent": round(100 * shared / smaller)})
    pairs.sort(key=lambda p: (-p["shared"], -p["percent"]))
    return pairs


def for_attempt(exam_id, attempt_id, **kw):
    return [p for p in report(exam_id, **kw) if attempt_id in (p["a_id"], p["b_id"])]
