"""Pure helpers for the donation lifecycle: transition rules and the delivery timeline."""
import config


def can_transition(current, target):
    return target in config.STATUS_TRANSITIONS.get(current, [])


def allowed_next(current):
    return list(config.STATUS_TRANSITIONS.get(current, []))


def build_timeline(current, events):
    """Steps for the Amazon-style tracker.

    events: rows with status / created_at / note (oldest first). Each step has
    state = done | current | upcoming | exception, plus the real timestamp when it happened.
    """
    by_status = {}
    for e in events:
        by_status.setdefault(e["status"], e)  # first time the status was reached

    def step(status, state):
        info = config.STATUS_INFO[status]
        ev = by_status.get(status)
        return {"status": status, "label": info["label"], "text": info["text"], "actor": info["actor"],
                "state": state, "time": ev["created_at"] if ev else None,
                "note": (ev or {}).get("note") or ""}

    steps = []
    if current in config.EXCEPTIONAL_STATUSES:
        for s in config.HAPPY_PATH:
            if s in by_status:
                steps.append(step(s, "done"))
        steps.append(step(current, "exception"))
        return steps

    reached = config.HAPPY_PATH.index(current) if current in config.HAPPY_PATH else 0
    for i, s in enumerate(config.HAPPY_PATH):
        if i < reached:
            state = "done"
        elif i == reached:
            state = "done" if current == "Delivered" else "current"
        else:
            state = "upcoming"
        steps.append(step(s, state))
    return steps
