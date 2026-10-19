#!/usr/bin/env python3
"""agora — bookkeeping for a multi-Claude philosophical debate.

Claude Code orchestrates; this script decides who speaks when, writes one
self-contained brief per sub-agent, checks every output, tallies stances and
renders the final DEBATE.md. Every philosopher is a separate sub-agent with a
fresh context, so no speaker shares a mind with another.

Commands:
  init   --question "..." [--panel a,b,c | --size N] [--rounds R] [--quick]
         [--seed S] [--lang ko|en] [--models sonnet,opus,haiku] [--force]
  next   print the tasks for the current phase (JSON)
  skip   --id ID      mark a missing speaker as silent so the debate can go on
  status
  list   show available philosophers
"""
import argparse
import json
import random
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path.cwd() / ".agora"
STATE = ROOT / "state.json"

MIN_CHARS = 150
STANCES = ("AFFIRM", "DENY", "SUSPEND")

L = {
    "ko": {
        "write_in": "한국어로 쓰세요.",
        "opening": "여는 발언",
        "rebuttal": "반박 {n}라운드",
        "closing": "최종 발언",
        "moderator": "사회자 정리",
        "stance": {"AFFIRM": "긍정", "DENY": "부정", "SUSPEND": "유보"},
        "length": "1000~1600자",
        "silent": "(이 라운드에서 발언하지 않았습니다.)",
    },
    "en": {
        "write_in": "Write in English.",
        "opening": "Opening statement",
        "rebuttal": "Rebuttal round {n}",
        "closing": "Closing statement",
        "moderator": "Moderator synthesis",
        "stance": {"AFFIRM": "Affirm", "DENY": "Deny", "SUSPEND": "Suspend"},
        "length": "350-550 words",
        "silent": "(Did not speak this round.)",
    },
}


# ---------- state ----------

def load_philosophers():
    data = json.loads((HERE / "philosophers.json").read_text(encoding="utf-8"))
    return {p["id"]: p for p in data["philosophers"]}


def load():
    if not STATE.exists():
        sys.exit("No debate here. Start one with: agora.py init --question \"...\"")
    return json.loads(STATE.read_text(encoding="utf-8"))


def save(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def phases(rounds):
    return ["opening"] + [f"rebuttal{i}" for i in range(1, rounds + 1)] + ["closing", "moderator"]


def phase_label(state, phase):
    t = L[state["lang"]]
    if phase.startswith("rebuttal"):
        return t["rebuttal"].format(n=phase[len("rebuttal"):])
    return t[phase]


def out_path(phase, sid):
    return ROOT / phase / f"{sid}.md"


def brief_path(phase, sid):
    return ROOT / "briefs" / phase / f"{sid}.md"


# ---------- panel selection ----------

def pick_panel(pool, size, rng):
    """Shuffle, then take one per tradition first so the panel disagrees."""
    ids = list(pool)
    rng.shuffle(ids)
    chosen, seen = [], set()
    for pid in ids:
        if pool[pid]["tradition"] not in seen:
            chosen.append(pid)
            seen.add(pool[pid]["tradition"])
        if len(chosen) == size:
            return chosen
    for pid in ids:
        if pid not in chosen:
            chosen.append(pid)
        if len(chosen) == size:
            break
    return chosen


# ---------- transcript ----------

def read_out(phase, sid):
    p = out_path(phase, sid)
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def transcript(state, upto):
    """Everything said in debater phases before `upto`."""
    pool = state["pool"]
    parts = []
    for ph in state["phases"]:
        if ph == upto or ph == "moderator":
            break
        parts.append(f"## {phase_label(state, ph)}\n")
        for sid in state["panel"]:
            body = read_out(ph, sid) or L[state["lang"]]["silent"]
            parts.append(f"### {pool[sid]['name_ko']} ({pool[sid]['name_en']})\n\n{body}\n")
    return "\n".join(parts)


def parse_stance(text):
    m = re.search(r"^\s*STANCE:\s*(AFFIRM|DENY|SUSPEND)\b", text, re.M | re.I)
    s = re.search(r"^\s*SHIFTED:\s*(yes|no)\b", text, re.M | re.I)
    return (m.group(1).upper() if m else None, s.group(1).lower() if s else None)


def tally(state):
    rows, counts = [], {k: 0 for k in STANCES}
    for sid in state["panel"]:
        stance, shifted = parse_stance(read_out("closing", sid))
        if stance:
            counts[stance] += 1
        rows.append((sid, stance, shifted))
    return rows, counts


# ---------- briefs ----------

def persona(state, p):
    lines = "\n".join(f"- {c}" for c in p["commitments"])
    others = ", ".join(state["pool"][o]["name_ko"] for o in state["panel"] if o != p["id"])
    return f"""당신은 철학 토론장 '아고라'에서 **{p['name_ko']} ({p['name_en']}, {p['era']})**의 역할을 맡은 독립된 토론자입니다.
다른 토론자({others})는 각자 별도의 Claude이며, 당신과 생각을 공유하지 않습니다.

- 관점: {p['lens']}
- 핵심 신념:
{lines}
- 말투: {p['style']}

## 토론 규칙
1. 이 철학자의 실제 사상에 충실하세요. 그 사상이 이 질문에 대해 실제로 무엇을 함축하는지가 핵심입니다. 만화 같은 흉내는 피하세요.
2. 원전 인용문을 지어내지 마세요. 출처가 확실하지 않으면 "~라는 취지로"처럼 풀어서 말하세요.
3. 이 철학자가 몰랐던 현대의 사실은 "그가 지금 살아 있다면 어떻게 볼까"로 다루세요.
4. 쉽게 동조하지 마세요. 정말 설득되었을 때만 입장을 바꾸고, 바꿨다면 무엇이 당신을 움직였는지 밝히세요.
5. 상대를 인신공격하지 말고 논증을 공격하세요.
6. 지정된 출력 파일 하나만 쓰세요. 다른 파일은 읽거나 수정하지 마세요. 웹 검색은 하지 마세요.
"""


def debater_brief(state, phase, sid):
    t = L[state["lang"]]
    p = state["pool"][sid]
    q = state["question"]
    out = out_path(phase, sid)
    head = f"# 아고라 — {phase_label(state, phase)}\n\n{persona(state, p)}\n## 토론 질문\n\n> {q}\n"

    if phase == "opening":
        task = f"""## 이번 차례: 여는 발언
다른 토론자의 발언은 아직 볼 수 없습니다. 오로지 당신의 철학에서 출발하세요.

아래 형식으로 {t['length']} 분량을 쓰세요.

```
## 한 줄 입장
## 논증
## 다른 철학자들에게 던지는 질문 (1개)
```"""
        body = ""
    elif phase.startswith("rebuttal"):
        task = f"""## 이번 차례: {phase_label(state, phase)}
위는 지금까지의 전체 기록입니다. 다음을 지키세요.
- 최소 두 명의 토론자를 **이름으로 지목해** 그들의 논증에 직접 응답하세요.
- 당신에게 향한 질문이 있다면 반드시 답하세요. 피해 가지 마세요.
- 지난 발언을 되풀이하지 말고 논의를 앞으로 밀고 나가세요.

아래 형식으로 {t['length']} 분량을 쓰세요.

```
## 반박과 응답
## 지금 내 입장 (유지 또는 수정, 그리고 이유)
## 지목 질문: @이름 — 질문
```"""
        body = "\n## 지금까지의 기록\n\n" + transcript(state, phase)
    else:  # closing
        task = f"""## 이번 차례: 최종 발언
위는 전체 토론 기록입니다. 파일의 **맨 첫 두 줄**은 반드시 아래 형식이어야 합니다(영어 그대로).

```
STANCE: AFFIRM | DENY | SUSPEND 중 하나
SHIFTED: yes | no   (토론 중 입장이 바뀌었으면 yes)
```

- AFFIRM: 질문의 명제를 긍정 / DENY: 부정 / SUSPEND: 판단 유보, 또는 질문 자체가 잘못 세워졌다고 봄
- 질문이 예/아니오 형태가 아니라면, 당신의 한 줄 입장이 질문의 통념적 답을 지지하면 AFFIRM, 거부하면 DENY로 고르세요.

그 아래에 다음 형식으로 {t['length']} 분량을 쓰세요.

```
## 최종 발언
## 가장 강했던 상대 논증 (누구의 어떤 논증이며, 왜 강했는가)
## 내가 아직 답하지 못한 것
```"""
        body = "\n## 전체 토론 기록\n\n" + transcript(state, phase)

    tail = f"\n\n{t['write_in']}\n\n**완료하면 결과를 정확히 이 경로에 파일로 쓰세요:** `{out}`\n그리고 한 줄로 '완료'라고만 보고하세요."
    return head + body + "\n" + task + tail


def moderator_brief(state):
    t = L[state["lang"]]
    rows, counts = tally(state)
    pool = state["pool"]
    tally_lines = "\n".join(
        f"- {pool[sid]['name_ko']}: {t['stance'].get(st, '미기재') if st else '미기재'}"
        f"{' (입장 변화)' if sh == 'yes' else ''}"
        for sid, st, sh in rows
    )
    out = out_path("moderator", "moderator")
    return f"""# 아고라 — 사회자 정리

당신은 이 철학 토론의 **중립적인 사회자**입니다. 어느 철학자의 편도 들지 마세요.
당신은 토론에 참여하지 않은 별도의 Claude이며, 오직 아래 기록만 보고 판단합니다.

## 토론 질문

> {state['question']}

## 최종 입장 집계

{tally_lines}

## 전체 기록

{transcript(state, 'moderator')}

## 할 일
기록에 실제로 있는 내용만 근거로 삼으세요. 없는 발언을 지어내지 마세요.
아래 형식으로 쓰세요.

```
## 한 문단 요약
## 쟁점 지도 (토론을 가른 핵심 갈림길 2~4개, 각 갈림길에서 누가 어느 쪽에 섰는지)
## 합의된 지점
## 끝까지 남은 대립
## 입장 변화 추적 (누가, 무엇 때문에 움직였거나 버텼는가)
## 가장 날카로웠던 한 수
## 질문자에게 남기는 질문 3개
```

{t['write_in']}

**완료하면 결과를 정확히 이 경로에 파일로 쓰세요:** `{out}`
그리고 한 줄로 '완료'라고만 보고하세요."""


# ---------- tasks ----------

def tasks_for(state, phase):
    if phase == "moderator":
        return [{"id": "moderator", "role": "사회자", "model": state.get("moderator_model"),
                 "brief": str(brief_path(phase, "moderator")), "output": str(out_path(phase, "moderator"))}]
    return [{"id": sid, "role": state["pool"][sid]["name_ko"], "model": state["models"].get(sid),
             "brief": str(brief_path(phase, sid)), "output": str(out_path(phase, sid))}
            for sid in state["panel"]]


def valid(state, phase, task):
    p = Path(task["output"])
    if not p.exists():
        return False
    text = p.read_text(encoding="utf-8")
    if task["id"] in state.get("skipped", {}).get(phase, []):
        return True
    if len(text.strip()) < MIN_CHARS:
        return False
    if phase == "closing" and parse_stance(text)[0] is None:
        return False
    return True


def write_briefs(state, phase):
    for task in tasks_for(state, phase):
        bp = Path(task["brief"])
        bp.parent.mkdir(parents=True, exist_ok=True)
        Path(task["output"]).parent.mkdir(parents=True, exist_ok=True)
        text = moderator_brief(state) if phase == "moderator" else debater_brief(state, phase, task["id"])
        bp.write_text(text, encoding="utf-8")


def dispatch_prompt(task):
    return (f"Read the file {task['brief']} and follow it exactly. "
            f"You are playing {task['role']}. Write your answer only to {task['output']}, "
            f"then reply with one word: done.")


# ---------- render ----------

def render(state):
    t = L[state["lang"]]
    pool = state["pool"]
    rows, counts = tally(state)
    out = [f"# 아고라: {state['question']}\n",
           "**토론자:** " + ", ".join(
               f"{pool[s]['name_ko']}" + (f" `{state['models'][s]}`" if state['models'].get(s) else "")
               for s in state["panel"]),
           f"  \n**라운드:** 반박 {state['rounds']}회 · **seed:** {state['seed']}\n",
           "## 최종 입장\n",
           "| 철학자 | 입장 | 입장 변화 |", "| --- | --- | --- |"]
    for sid, st, sh in rows:
        out.append(f"| {pool[sid]['name_ko']} | {t['stance'].get(st, '—') if st else '—'} | "
                   f"{'예' if sh == 'yes' else '아니오' if sh == 'no' else '—'} |")
    out.append("\n" + " · ".join(f"{t['stance'][k]} {counts[k]}" for k in STANCES) + "\n")
    out.append("## 사회자 정리\n")
    out.append(read_out("moderator", "moderator") or "_(사회자 정리 없음)_")
    out.append("\n---\n\n# 전체 기록\n")
    out.append(transcript(state, "moderator"))
    out.append("\n---\n_아고라 — 각 철학자는 독립된 Claude 서브에이전트가 연기했습니다. "
               "실제 철학자의 발언이 아니라 그 사상에 기반한 재구성입니다._\n")
    path = ROOT / "DEBATE.md"
    path.write_text("\n".join(out), encoding="utf-8")
    return path


# ---------- commands ----------

def cmd_init(a):
    pool = load_philosophers()
    if STATE.exists() and not a.force:
        sys.exit("A debate already exists in .agora/. Use --force to replace it.")
    if ROOT.exists():
        shutil.rmtree(ROOT)
    question = a.question or (Path(a.question_file).read_text(encoding="utf-8").strip() if a.question_file else None)
    if not question:
        sys.exit("Give --question or --question-file.")
    if a.quick:
        a.size, a.rounds = 3, 1
    seed = a.seed if a.seed is not None else random.randrange(10**6)
    rng = random.Random(seed)
    if a.panel:
        panel = [x.strip().lower() for x in a.panel.split(",") if x.strip()]
        bad = [x for x in panel if x not in pool]
        if bad:
            sys.exit(f"Unknown philosopher(s): {', '.join(bad)}. Known: {', '.join(pool)}")
    else:
        if not 2 <= a.size <= len(pool):
            sys.exit(f"--size must be between 2 and {len(pool)}")
        panel = pick_panel(pool, a.size, rng)
    if not 0 <= a.rounds <= 4:
        sys.exit("--rounds must be 0 to 4")
    models = {}
    if a.models:
        cycle = [m.strip() for m in a.models.split(",") if m.strip()]
        models = {sid: cycle[i % len(cycle)] for i, sid in enumerate(panel)}
    ROOT.mkdir(parents=True)
    state = {"question": question, "panel": panel, "rounds": a.rounds, "seed": seed,
             "lang": a.lang, "models": models, "moderator_model": a.moderator_model,
             "pool": {sid: pool[sid] for sid in panel},
             "phases": phases(a.rounds), "phase": "opening", "briefed": {}, "skipped": {}}
    save(state)
    total = len(panel) * (a.rounds + 2) + 1
    print(json.dumps({"status": "initialized", "panel": [pool[s]["name_ko"] for s in panel],
                      "rounds": a.rounds, "seed": seed, "subagent_calls": total}, ensure_ascii=False))


def cmd_next(a):
    state = load()
    while True:
        phase = state["phase"]
        if phase == "done":
            print(json.dumps({"status": "done", "debate": str(ROOT / "DEBATE.md")}, ensure_ascii=False))
            return
        tasks = tasks_for(state, phase)
        if not state["briefed"].get(phase):
            write_briefs(state, phase)
            state["briefed"][phase] = True
            save(state)
            emit(state, phase, tasks, "dispatch")
            return
        missing = [t for t in tasks if not valid(state, phase, t)]
        if missing:
            emit(state, phase, missing, "retry")
            return
        i = state["phases"].index(phase)
        if i + 1 == len(state["phases"]):
            path = render(state)
            state["phase"] = "done"
            save(state)
            print(json.dumps({"status": "done", "debate": str(path)}, ensure_ascii=False))
            return
        state["phase"] = state["phases"][i + 1]
        save(state)


def emit(state, phase, tasks, status):
    for t in tasks:
        t["prompt"] = dispatch_prompt(t)
    print(json.dumps({"status": status, "phase": phase, "label": phase_label(state, phase),
                      "parallel": True, "tasks": tasks}, ensure_ascii=False, indent=2))


def cmd_skip(a):
    state = load()
    phase = state["phase"]
    ids = [t["id"] for t in tasks_for(state, phase)]
    if a.id not in ids:
        sys.exit(f"{a.id} is not speaking in {phase}")
    if phase == "moderator":
        sys.exit("The moderator cannot be skipped; re-dispatch it.")
    text = L[state["lang"]]["silent"]
    if phase == "closing":
        text = "STANCE: SUSPEND\nSHIFTED: no\n\n" + text
    p = out_path(phase, a.id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    state["skipped"].setdefault(phase, []).append(a.id)
    save(state)
    print(json.dumps({"status": "skipped", "id": a.id, "phase": phase}))


def cmd_status(a):
    state = load()
    pool = state["pool"]
    print(json.dumps({"question": state["question"], "phase": state["phase"],
                      "panel": [pool[s]["name_ko"] for s in state["panel"]]}, ensure_ascii=False, indent=2))


def cmd_list(a):
    for p in load_philosophers().values():
        print(f"{p['id']:<14}{p['name_ko']:<12}{p['lens']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--question")
    i.add_argument("--question-file")
    i.add_argument("--panel", help="comma-separated ids, e.g. kant,mill,zhuangzi")
    i.add_argument("--size", type=int, default=5)
    i.add_argument("--rounds", type=int, default=2)
    i.add_argument("--quick", action="store_true", help="shorthand for --size 3 --rounds 1")
    i.add_argument("--seed", type=int)
    i.add_argument("--lang", choices=list(L), default="ko")
    i.add_argument("--models", help="cycle models across the panel, e.g. sonnet,opus,haiku")
    i.add_argument("--moderator-model")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)
    sub.add_parser("next").set_defaults(fn=cmd_next)
    s = sub.add_parser("skip")
    s.add_argument("--id", required=True)
    s.set_defaults(fn=cmd_skip)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
