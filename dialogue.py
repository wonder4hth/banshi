"""Local rule-based dialogue engine.

No external LLM call, no API key needed. Character "voice" is produced by
combining two persona axes chosen at agent-creation time:
  - speaking_style        (说话风格): how the character talks
  - distraction_attitude  (对待分心态度): how they react when you don't finish

Swap-in point for a real LLM later: replace `generate_reply()` /
`generate_outcome_line()` bodies with an API call, keep the same signature.
"""
import random
import re

STYLES = {
    "quiet": "寡言安静",
    "gentle": "温柔鼓励",
    "energetic": "元气活泼",
    "calm": "冷静克制",
}

ATTITUDES = {
    "tolerant": "包容谅解",
    "regretful": "略带遗憾",
    "stern": "严厉责备",
}

AVATARS = [
    {"id": "fox", "emoji": "🦊", "color": "#e8996b"},
    {"id": "cat", "emoji": "🐱", "color": "#c9a4e0"},
    {"id": "owl", "emoji": "🦉", "color": "#8a9bd6"},
    {"id": "wolf", "emoji": "🐺", "color": "#7d8ba1"},
    {"id": "rabbit", "emoji": "🐰", "color": "#f0a8bd"},
    {"id": "dragon", "emoji": "🐉", "color": "#6ec2a8"},
    {"id": "moon", "emoji": "🌙", "color": "#5b6b9e"},
    {"id": "star", "emoji": "✨", "color": "#e0b84f"},
]

# ---- greetings shown when entering a chat -------------------------------
INTRO_LINES = {
    "quiet": ["（安静地看了你一眼）你来了。", "（点了点头）……坐吧。"],
    "gentle": ["你来啦，今天感觉怎么样？", "看到你我就放心了，今天也要加油哦。"],
    "energetic": ["哦哦你终于来啦！今天想做点什么！", "嗨！好久没聊啦，快跟我说说！"],
    "calm": ["你来了。今天准备好了吗？", "按时抵达，很好。说说今天的安排。"],
}

# ---- generic idle chat replies (no task keyword detected) ---------------
IDLE_REPLIES = {
    "quiet": ["……嗯。", "是这样啊。", "（轻轻点头）", "还有别的事吗？"],
    "gentle": ["嗯嗯，我在听呢。", "没关系，慢慢说。", "辛苦你了。"],
    "energetic": ["原来如此！继续说继续说！", "哈哈是嘛！", "然后呢然后呢！"],
    "calm": ["我明白了。", "继续。", "这样啊，有道理。"],
}

# ---- flavor line shown right before the 发现任务 modal pops up ----------
TASK_OFFER_LINES = {
    "quiet": "（推了推面前的纸）……要不要接一份委托。",
    "gentle": "要不要一起定个小委托？我陪着你。",
    "energetic": "机会来啦！要不要接个委托冲一把！",
    "calm": "看来是时候安排一项委托了。",
}

# ---- keyword triggers for "发现任务" -------------------------------------
# Deliberately broad — the point of the app is to gamify *any* real-world
# focus task (study, chores, errands, exercise, work…), not just studying.
FOCUS_KEYWORDS = [
    # 学习/工作
    "专注", "作业", "学习", "复习", "背", "写", "任务", "工作", "看书",
    "刷题", "论文", "读书", "备考", "自习", "赶", "效率", "代码",
    "项目", "方案", "报告", "ppt", "邮件",
    # 家务/整理
    "整理", "打扫", "收拾", "收纳", "洗", "叠", "扔垃圾", "倒垃圾", "拖地", "擦",
    # 跑腿/事务
    "跑腿", "取快递", "买菜", "买东西", "超市", "采购", "寄快递", "还书", "取号", "排队", "预约", "办事",
    # 运动/锻炼
    "锻炼", "运动", "跑步", "健身", "拉伸", "瑜伽",
    # 通用意图短语
    "该做", "得做", "搞定", "处理一下", "弄完", "搞完", "冲一下", "冲刺", "开始做",
    "focus", "study",
]

# Fantasy/game-world quest flavor — spans combat, escort, chores, exploration
# etc. so the pool never reads as "studying quests only". This is also the
# fallback pool for the LLM engine when it can't be reached.
TASK_LIBRARY = [
    ("讨伐林地哥布林", "清剿骚扰村庄的哥布林小队"),
    ("护送商队至边境", "一路护送满载货物的商队安全抵达"),
    ("清扫遗迹尘埃", "把废弃遗迹里堆积的尘埃杂物清理干净"),
    ("巡查夜间城墙", "沿城墙巡逻一圈，确保无异常"),
    ("寻回走失的信鸽", "在城郊寻找并带回走丢的信鸽"),
    ("采买炼金材料", "前往集市采购炼金术所需的材料"),
    ("整理魔法藏书阁", "把杂乱的藏书重新归类摆放整齐"),
    ("修补破损的营帐", "把训练营里破损的帐篷缝补好"),
    ("击退训练场魔像", "挑战训练场里的木桩魔像，磨炼体魄"),
    ("抄录古卷文书", "静心誊抄一份重要的古老卷宗"),
    ("送交紧急情报", "把一份紧急情报送到指定地点"),
    ("清点仓库物资", "把仓库里堆积的物资逐一清点归档"),
    ("追踪迷路的旅人", "在林间小径寻找并带回迷路的旅人"),
    ("打扫旅店客房", "把旅店里凌乱的客房重新打扫整洁"),
    ("收集晨露药草", "趁天亮前采集山间的晨露药草"),
    ("修复受损的魔导器", "检查并修复出故障的魔导装置"),
    ("布置节庆灯笼", "为即将到来的节日布置装饰灯笼"),
    ("侦查废弃矿洞", "深入废弃矿洞查探是否安全"),
]

# ---- outcome reaction matrix: [style][attitude][outcome] -> [lines] -----
# outcome in {complete, distracted, gave_up}
OUTCOME_LINES = {
    "quiet": {
        "tolerant": {
            "complete": ["（唇角极淡地扬起）辛苦了，这份委托没有疏漏。", "（点头）……做得不错。"],
            "distracted": ["（轻轻摇头）中途略有停顿，不过总算收尾了。", "……没关系，能坚持完就好。"],
            "gave_up": ["（合上书页，平静地看向你）无妨，改日可以重新接下这份委托。", "……累了就歇着，下次再来。"],
        },
        "regretful": {
            "complete": ["（微微颔首）总算是完成了，辛苦。", "……做到了，很好。"],
            "distracted": ["（叹了口气）……有点可惜，不过还是收尾了。", "……本可以更专心一些的。"],
            "gave_up": ["（沉默片刻）……这次没能坚持，有些遗憾。", "……罢了，下次再看吧。"],
        },
        "stern": {
            "complete": ["（难得点头）这次总算合格了。", "……还算像样。"],
            "distracted": ["（皱眉）……分心了就是分心了，下次收着点。", "……这样可不行，自己清楚。"],
            "gave_up": ["（放下笔，语气平淡）半途而废，不是我教你的样子。", "……这份委托，你自己心里该有数。"],
        },
    },
    "gentle": {
        "tolerant": {
            "complete": ["太棒了，你做到了！我为你骄傲。", "辛苦啦，休息一下吧，你很棒。"],
            "distracted": ["没关系呀，中途分心也很正常，你还是坚持下来了。", "已经很努力了，下次我们再试试。"],
            "gave_up": ["没关系的，今天状态不好就歇一歇，我陪着你呢。", "不用勉强自己，下次我们再一起接这份委托。"],
        },
        "regretful": {
            "complete": ["完成啦，真好，辛苦你了。", "做到了呢，我就知道你可以。"],
            "distracted": ["嗯……有点可惜没能一直专心，不过你已经很努力了。", "下次我们再试着更专注一点，好吗？"],
            "gave_up": ["唉，这次没能坚持下来，有点小遗憾呢……不过没关系。", "算了，下次我陪你重新试一次。"],
        },
        "stern": {
            "complete": ["嗯，这才对嘛，继续保持！", "做到了！但下次要更稳一点哦。"],
            "distracted": ["中途分心可不太好哦，我们要更专注一些。", "这次不算完美，下次要更认真一点知道吗。"],
            "gave_up": ["这样半途而废是不行的，我们得说好下次一定坚持。", "不可以轻易放弃哦，我相信你能做到的。"],
        },
    },
    "energetic": {
        "tolerant": {
            "complete": ["耶！委托完成！你超厉害的！", "太棒啦！这就是我的伙伴！"],
            "distracted": ["中间溜号了一下也没关系啦，还是完成了对吧！", "嘿嘿，下次我们再冲刺得更猛一点！"],
            "gave_up": ["没事没事，这次算了，下次咱们再来一局！", "累了就歇会儿嘛，委托还在这儿等你！"],
        },
        "regretful": {
            "complete": ["哦耶完成啦！不过感觉你还能更快一点哈哈。", "搞定！干得不错！"],
            "distracted": ["啊——中途分心了呀，有点可惜诶，不过还是撑过来了！", "下次可得更专心哦，不然多浪费！"],
            "gave_up": ["唉呀，这次没坚持住，有点小遗憾……不过下次一定行！", "算了算了，下次我们卷土重来！"],
        },
        "stern": {
            "complete": ["就该这样！干得漂亮！", "完成！但别骄傲，下次继续冲！"],
            "distracted": ["喂，怎么能分心呢！下次给我盯紧点！", "这次不合格啊，专注力要跟上！"],
            "gave_up": ["半途而废可不是我认识的你！下次必须坚持到底！", "不行不行，这次太差劲了，下次绝对不许放弃！"],
        },
    },
    "calm": {
        "tolerant": {
            "complete": ["完成得很好，按部就班，值得肯定。", "任务已妥善完成，辛苦了。"],
            "distracted": ["中途有过波动，但结果尚可，无需苛责自己。", "过程有分心，属于正常范围，继续保持节奏即可。"],
            "gave_up": ["这次没能完成，也没关系，调整状态后再来。", "半途中止，理解。下次再战。"],
        },
        "regretful": {
            "complete": ["完成了，不过节奏还可以更稳一些。", "结果达成，过程稍有波动，值得注意。"],
            "distracted": ["中途分心，略显遗憾，下次可以更专注。", "结果尚可，但过程不够理想。"],
            "gave_up": ["未能完成，这一点略感遗憾，希望下次能改善。", "中止了，有些可惜，下次调整一下节奏。"],
        },
        "stern": {
            "complete": ["完成，达到预期，继续保持这个标准。", "结果合格，但仍有提升空间。"],
            "distracted": ["中途分心，这是需要改进的地方，下次注意。", "过程不够专注，标准需要提高。"],
            "gave_up": ["半途而废，这个结果不能接受，下次必须完成。", "中止委托，说明专注力仍需锻炼，下次不能再这样。"],
        },
    },
}

STORY_TEMPLATES = {
    "complete": "与{agent}接取【{task}】{minutes}分钟委托，圆满归来",
    "distracted": "与{agent}接取【{task}】{minutes}分钟委托，中途走神，但仍完成",
    "gave_up": "与{agent}接取【{task}】{minutes}分钟委托，中途放弃，改日再战",
}


def detects_focus_intent(text: str) -> bool:
    return any(kw in text for kw in FOCUS_KEYWORDS)


def pick_task():
    name, desc = random.choice(TASK_LIBRARY)
    return name, desc


def generate_intro(style: str) -> str:
    return random.choice(INTRO_LINES.get(style, INTRO_LINES["calm"]))


def generate_idle_reply(style: str) -> str:
    return random.choice(IDLE_REPLIES.get(style, IDLE_REPLIES["calm"]))


def generate_task_offer_line(style: str) -> str:
    return TASK_OFFER_LINES.get(style, TASK_OFFER_LINES["calm"])


def generate_outcome_line(style: str, attitude: str, outcome: str) -> str:
    style = style if style in OUTCOME_LINES else "calm"
    attitude = attitude if attitude in OUTCOME_LINES[style] else "tolerant"
    outcome = outcome if outcome in OUTCOME_LINES[style][attitude] else "complete"
    return random.choice(OUTCOME_LINES[style][attitude][outcome])


def generate_reply(style: str, user_text: str, force: bool = False):
    """Return (reply_text, task_offer) — task_offer is (name, desc) or None.

    `force=True` skips the keyword gate entirely — used when the user
    manually summons a task via the chat page's bottom toolbar, to cover
    cases the automatic keyword/LLM detection missed.
    """
    if force or detects_focus_intent(user_text):
        name, desc = pick_task()
        return generate_task_offer_line(style), (name, desc)
    return generate_idle_reply(style), None


def build_story_entry(agent_name: str, task_name: str, minutes: int, outcome: str) -> str:
    tpl = STORY_TEMPLATES.get(outcome, STORY_TEMPLATES["complete"])
    return tpl.format(agent=agent_name, task=task_name, minutes=minutes)
