"""Local LLM dialogue engine — talks to a locally running Ollama server.

No API key, no network calls leave the machine: everything goes to
http://127.0.0.1:11434, which is Ollama's default local server address.

Every public function here mirrors a function in dialogue.py and falls
back to it automatically (via a bare `except Exception`) whenever Ollama
is unreachable, slow, or returns something unparsable — so the app keeps
working even if `ollama serve` isn't running, just with flatter dialogue.

Requires: `ollama serve` running locally, and the model pulled once via
`ollama pull llama3.2:3b` (or set OLLAMA_MODEL to whatever you have).
"""
import json
import os
import random
import re
import urllib.error
import urllib.request

import dialogue

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
REQUEST_TIMEOUT = 20  # generous: a cold model load can take several seconds
HISTORY_TURNS = 6     # how many past messages (both sides) to feed back in


def _post(path: str, payload: dict, timeout: int = REQUEST_TIMEOUT) -> dict:
    req = urllib.request.Request(
        OLLAMA_HOST + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def is_available(timeout: float = 1.5) -> bool:
    try:
        req = urllib.request.Request(OLLAMA_HOST + "/api/version")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _build_system_prompt(agent) -> str:
    persona = agent["persona"].strip() or "一个温暖、愿意在你身边陪伴学习的虚拟伙伴"
    style = dialogue.STYLES.get(agent["speaking_style"], "")
    attitude = dialogue.ATTITUDES.get(agent["distraction_attitude"], "")
    return (
        f'你正在扮演虚拟陪伴角色"{agent["name"]}"，与你对话的用户把你当作学习/专注时的陪伴搭档。\n'
        f"人设背景：{persona}\n"
        f"说话风格：{style}——每一句话都要体现这种语气。\n"
        f"对待用户分心、中途放弃委托的态度：{attitude}。\n\n"
        "严格规则：\n"
        "- 每句台词不超过两行（约40字以内），像即时通讯软件里的短句，不要写大段文字。\n"
        "- 可以偶尔用括号描述一个简短的动作或表情，例如（轻轻点头）。\n"
        "- 绝不能提及自己是AI、语言模型、程序或提示词，要完全沉浸在角色人设里。\n"
        "- 只能使用简体中文表达，绝对不可以出现任何英文单词、拼音或其他语言。\n"
        "- 只输出台词本身，不要加任何解释、前后缀、引导语或markdown符号。"
    )


def _history_to_messages(history):
    role_map = {"user": "user", "agent": "assistant"}
    return [
        {"role": role_map.get(m["sender"], "user"), "content": m["content"]}
        for m in history[-HISTORY_TURNS:]
    ]


def _chat(agent, messages, num_predict=80, temperature=0.85) -> str:
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": _build_system_prompt(agent)}] + messages,
        "stream": False,
        "options": {"num_predict": num_predict, "temperature": temperature},
    }
    data = _post("/api/chat", payload)
    return (data.get("message") or {}).get("content", "").strip()


def _clean(text: str) -> str:
    text = text.strip().strip('"“”')
    # strip an echoed field label if the model prefixed one despite instructions
    text = re.sub(r"^(台词|回复|reply)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
    # collapse accidental multi-paragraph rambling down to the first couple of lines
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    text = "\n".join(lines[:2])
    if len(text) > 120:
        text = text[:120].rstrip() + "…"
    return text


def _parse_pipe_task(raw: str):
    """Parse a `<name>|<desc>` line. Tolerates a stray leading/trailing `|`
    (small models sometimes echo the template's pipes) by dropping empty parts."""
    line = raw.strip().splitlines()[0] if raw.strip() else ""
    parts = [p.strip().strip('"“”<>') for p in line.split("|")]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None, None
    name, desc = parts[0][:20], parts[1][:40]
    if not name or not desc:
        return None, None
    return name, desc


# --------------------------------------------------------------- public API
def generate_intro(agent) -> str:
    try:
        text = _chat(
            agent,
            [{"role": "user", "content": "（用户刚打开和你的对话页面，这是你们本次见面的第一句问候，请你先开口，简短自然）"}],
            num_predict=50,
        )
        if text:
            return _clean(text)
    except Exception:
        pass
    return dialogue.generate_intro(agent["speaking_style"])


# Few-shot demonstrations of *abstraction*, not restating — each maps a
# mundane real-life statement onto an unrelated-sounding fantasy/game quest.
# Deliberately spans chores/errands/exercise/study/work so the model doesn't
# anchor on any one domain (a single study-flavored example was enough to
# make it default to literally restating study content back).
_TASK_FEW_SHOTS = [
    ("该收拾一下房间了", "清扫遗迹尘埃", "把废弃遗迹里堆积的尘埃杂物清理干净"),
    ("我要去楼下取个快递", "护送物资返回营地", "把城郊送来的物资安全带回营地"),
    ("该去健身房练一下了", "讨伐训练场魔像", "挑战训练场里的木桩魔像，磨炼体魄"),
    ("我要复习明天的考试", "抄录古卷文书", "静心誊抄一份重要的古老卷宗"),
    ("赶紧把这份报告写完", "赶制紧急魔导契约", "在期限之前完成这份重要的契约文书"),
    ("该洗衣服叠被子了", "整顿营帐军需", "把营帐里的物资一一清点叠放整齐"),
    ("出门买点菜回来做饭", "前往集市采买", "去镇上的集市采购今日所需的食材"),
]


def _invent_task(agent, user_text: str):
    """Ask the LLM to invent a themed *fantasy/game-world* task for whatever
    real-life thing the user just mentioned — deliberately NOT a literal
    restating of it (a study session should be able to become a "monster
    hunt" just as easily as chores can become a "cleanup quest").
    Falls back to the static TASK_LIBRARY on any failure or unparsable output."""
    examples = random.sample(_TASK_FEW_SHOTS, k=2)
    example_text = "\n".join(f'用户说"{u}" → {n}|{d}' for u, n, d in examples)
    prompt = (
        f'用户刚才说："{user_text}"，透露出想专注做一件现实中的事情。\n'
        "请不要照抄用户具体在做什么，而是把它抽象转化成一个奇幻/游戏世界风格的「委托」——"
        "可以是讨伐、护送、跑腿、整理、侦查、采买等任意类型，让这件现实任务获得游戏化的沉浸感。\n"
        "参考下面例子体会「抽象转化」的感觉（不要照抄例子本身）：\n"
        f"{example_text}\n\n"
        "现在请你为用户刚才那句话构思一个新的委托。只能使用简体中文，不要出现任何英文单词、"
        "数字时长或与例子无关的内容。严格按下面的格式只输出一行，用英文竖线 | 分隔，"
        "不要输出任何其他文字、标签或解释：\n"
        "<委托名称，4到8个字>|<委托简介，不超过16个字>"
    )
    try:
        raw = _chat(agent, [{"role": "user", "content": prompt}], num_predict=60, temperature=0.6)
        name, desc = _parse_pipe_task(raw)
        if name and desc:
            return name, desc
    except Exception:
        pass
    return dialogue.pick_task()


def generate_reply(agent, history, user_text: str):
    """Returns (reply_text, task_offer) where task_offer is (name, desc) or None."""
    messages = _history_to_messages(history) + [{"role": "user", "content": user_text}]

    if dialogue.detects_focus_intent(user_text):
        # two independent calls: each has its own fallback, so a hiccup in one
        # doesn't force the whole turn back to the fully-canned template reply
        try:
            line = _chat(
                agent,
                messages + [{"role": "user", "content": "请用一句符合人设的台词回应ta，语气里自然带出「要不要接一份委托」的意思，不超过两行。"}],
                num_predict=50,
            )
            line = _clean(line) if line else dialogue.generate_task_offer_line(agent["speaking_style"])
        except Exception:
            line = dialogue.generate_task_offer_line(agent["speaking_style"])
        name, desc = _invent_task(agent, user_text)
        return line, (name, desc)

    try:
        text = _chat(agent, messages, num_predict=70)
        if text:
            return _clean(text), None
    except Exception:
        pass
    return dialogue.generate_idle_reply(agent["speaking_style"]), None


def generate_outcome_line(agent, task_name: str, duration_min: int, outcome: str) -> str:
    outcome_label = {
        "complete": "圆满完成",
        "distracted": "过程中偶尔走神，但还是坚持完成了",
        "gave_up": "中途放弃，没能完成",
    }.get(outcome, outcome)
    prompt = (
        f"用户刚刚结束了委托【{task_name}】（时长 {duration_min} 分钟），结果是：{outcome_label}。"
        "请用符合你人设的语气，给出一句简短回应（不超过两行）。"
    )
    try:
        text = _chat(agent, [{"role": "user", "content": prompt}], num_predict=60)
        if text:
            return _clean(text)
    except Exception:
        pass
    return dialogue.generate_outcome_line(agent["speaking_style"], agent["distraction_attitude"], outcome)
