#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""canteen-sniper 机关食堂抢位模拟器.

玩法:扮演游客,在国庆 7 天假期里抢机关食堂午餐名额.
每天 10:00 放号 30 个名额,和 34 个 AI 抢位手同场竞技.

灵感来源:2026 年国庆假期,超 70 个城市开放机关食堂/停车场给游客,
部分城市机关食堂名额开放首日即"一抢而空".本游戏纯属娱乐,
与任何真实单位、真实预约系统无关.

纯 Python 标准库,Python 3.10+.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter

BASE_SLOTS = 30   # 每天 10:00 放号名额
BONUS_SLOTS = 10  # "局长大气"事件追加名额
DAYS = 7          # 国庆 7 天假期
N_AI = 34         # AI 抢位手数量(手速/网速/运气三维属性)

# 策略:键 -> {显示名, 抢位分修正, 心态值变化, 描述}
STRATEGIES = {
    "camp": {
        "name": "提前蹲点",
        "bonus": 25,
        "mood": -25,
        "blurb": "提前 5 分钟蹲守,眼睛都不敢眨,专注度拉满",
    },
    "ontime": {
        "name": "掐点整抢",
        "bonus": 10,
        "mood": -5,
        "blurb": "10:00:00 准时开抢,拼的就是手速",
    },
    "chill": {
        "name": "随缘佛系",
        "bonus": -10,
        "mood": 20,
        "blurb": "佛系随缘,抢到是缘分,没抢到是命,顺便回血",
    },
}

STRATEGY_ALIASES = {
    "1": "camp", "2": "ontime", "3": "chill",
    "camp": "camp", "ontime": "ontime", "chill": "chill",
    "蹲点": "camp", "提前蹲点": "camp",
    "掐点": "ontime", "掐点整抢": "ontime",
    "佛系": "chill", "随缘": "chill", "随缘佛系": "chill",
}

# 结算称号:(最低顿数, 称号, 评语)
TITLES = [
    (7, "荣誉市民",
     "集齐 7 顿机关食堂午餐!居委会连夜给你印了荣誉市民奖状"
     "(并没有,但你的胃投了赞成票)。"),
    (5, "干饭王者",
     "7 天干了 5 顿以上,食堂阿姨已经记住你的脸,明天多给你打一勺。"),
    (3, "食堂编外人员",
     "混了个脸熟,打菜窗口的大叔开始问你\"还是老三样?\""),
    (1, "旅游特种兵",
     "好歹吃上过一顿,朋友圈素材 +1,没白来。"),
    (0, "白跑一趟",
     "7 天颗粒无收,建议改名\"食堂绝缘体\",明年再战。"),
]


def clamp(value, lo=0, hi=100):
    """把数值钳制在 [lo, hi] 区间."""
    return max(lo, min(hi, value))


class Sniper:
    """AI 抢位手:手速/网速/运气三维属性,均为 0-10."""

    __slots__ = ("speed", "net", "luck")

    def __init__(self, speed, net, luck):
        self.speed = speed
        self.net = net
        self.luck = luck


def make_snipers(rng, n=N_AI):
    """生成 n 个 AI 抢位手,属性随机."""
    return [Sniper(rng.randint(0, 10), rng.randint(0, 10), rng.randint(0, 10))
            for _ in range(n)]


def roll_event(rng):
    """掷每日随机事件,返回 (事件键, 描述)."""
    r = rng.random()
    if r < 0.15:
        return "boost", "【局长大气】食堂临时加 10 个名额!"
    if r < 0.30:
        return "lag", "【网络卡顿】你的请求一直在转圈圈……"
    if r < 0.40:
        return "family", "【亲情召唤】被家人喊去下馆子,错过放号(心态 +10)"
    if r < 0.50:
        return "tip", "【大爷秘籍】隔壁桌大爷传授抢位秘籍,抢位分永久 +8!"
    return None, "风平浪静,平平无奇的一天。"


def allocate_winners(scores, slots):
    """按分数取前 slots 名,返回获奖者下标集合.分数相同先到先得."""
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    return set(order[:slots])


def title_for(meals):
    """按 7 天累计午餐数返回 (称号, 评语)."""
    for need, name, blurb in TITLES:
        if meals >= need:
            return name, blurb
    raise AssertionError("unreachable")  # meals >= 0 必命中最后一档


class GameState:
    """一局游戏的状态:心态值/秘籍 buff/累计午餐/每日战报."""

    def __init__(self, rng):
        self.rng = rng
        self.snipers = make_snipers(rng)
        self.mood = 100
        self.buff = 0
        self.meals = 0
        self.days = []


def resolve_day(state, day, strategy_key):
    """结算一天.返回战报 dict,同时更新 state.

    strategy_key 必须是 STRATEGIES 的键,否则抛 ValueError.
    心态值归零时当天强制摆烂:不抢号,只回血.
    """
    rng = state.rng
    if strategy_key not in STRATEGIES:
        raise ValueError(f"未知策略: {strategy_key!r}")

    # 心态归零 -> 摆烂
    if state.mood <= 0:
        state.mood = clamp(state.mood + 35)
        rec = {"day": day, "strategy": "摆烂", "event": "心态归零,躺平回血",
               "slots": BASE_SLOTS, "rank": None, "got": False,
               "skip": False, "bail": True, "mood": state.mood,
               "meals": state.meals}
        state.days.append(rec)
        return rec

    strat = STRATEGIES[strategy_key]
    state.mood = clamp(state.mood + strat["mood"])

    event_key, event_desc = roll_event(rng)
    slots = BASE_SLOTS
    lag = False
    skip = False
    if event_key == "boost":
        slots += BONUS_SLOTS
    elif event_key == "lag":
        lag = True
    elif event_key == "family":
        skip = True
        state.mood = clamp(state.mood + 10)
    elif event_key == "tip":
        state.buff += 8

    got = False
    rank = None
    if not skip:
        player = 40 + strat["bonus"] + state.buff + rng.uniform(-10, 15)
        if lag:
            player -= 20
        scores = [player]
        for s in state.snipers:
            scores.append(40 + s.speed * 2 + s.net * 2 + s.luck * 2
                          + rng.uniform(-12, 12))
        winners = allocate_winners(scores, slots)
        got = 0 in winners
        rank = 1 + sum(1 for x in scores[1:] if x > player)
        if got:
            state.meals += 1

    rec = {"day": day, "strategy": strat["name"], "event": event_desc,
           "slots": slots, "rank": rank, "got": got,
           "skip": skip, "bail": False, "mood": state.mood,
           "meals": state.meals}
    state.days.append(rec)
    return rec


def describe_day(rec):
    """把单日战报渲染成中文文本."""
    lines = [f"Day {rec['day']}:策略[{rec['strategy']}]"]
    lines.append(f"  事件:{rec['event']}")
    if rec["bail"]:
        lines.append("  心态归零,今天摆烂,躺平回血。")
    elif rec["skip"]:
        lines.append("  错过放号,今天没得抢。")
    else:
        result = "抢到!" if rec["got"] else "没抢到"
        lines.append(f"  名额 {rec['slots']} 个,你的排名 #{rec['rank']} -> {result}")
    lines.append(f"  心态值 {rec['mood']},累计午餐 {rec['meals']} 顿")
    return "\n".join(lines)


def auto_game(seed):
    """自动演示一局:每天随机选策略(含心态归零时的强制摆烂)."""
    rng = random.Random(seed)
    state = GameState(rng)
    for day in range(1, DAYS + 1):
        key = rng.choice(list(STRATEGIES))
        resolve_day(state, day, key)
    return state


def print_summary(results):
    """打印多局自动演示的统计摘要."""
    meals = [s.meals for s in results]
    counts = Counter(title_for(m)[0] for m in meals)
    print(f"共 {len(results)} 局,7 天午餐数分布:")
    for lo, hi in [(7, 7), (5, 6), (3, 4), (1, 2), (0, 0)]:
        n = sum(1 for m in meals if lo <= m <= hi)
        bar = "█" * n
        print(f"  {lo}-{hi} 顿:{bar} ({n})")
    print("称号分布:")
    for _, name, _ in TITLES:
        print(f"  {name}:{counts.get(name, 0)}")
    print(f"平均每局 {sum(meals) / len(meals):.1f} 顿机关食堂")


def interactive():
    """中文人机交互模式.非 tty 环境返回退出码 2."""
    if not sys.stdin.isatty():
        print("canteen-sniper 需要交互式终端运行;非交互环境请使用 --auto。")
        return 2
    rng = random.Random()
    state = GameState(rng)
    print("=" * 42)
    print("机关食堂抢位模拟器 canteen-sniper")
    print("国庆 7 天,每天 10:00 放号 30 个午餐名额,")
    print("和 34 个 AI 抢位手同场竞技,祝你好胃口!")
    print("=" * 42)
    for day in range(1, DAYS + 1):
        print(f"\n—— 第 {day} 天 —— 心态值 {state.mood} | "
              f"秘籍加成 +{state.buff} | 已抢 {state.meals} 顿")
        if state.mood <= 0:
            print("心态归零,今天只能摆烂回血……")
            rec = resolve_day(state, day, "chill")
            print(describe_day(rec))
            time.sleep(0.4)
            continue
        for key, s in STRATEGIES.items():
            print(f"  {key}: {s['name']}({s['blurb']},"
                  f"抢位 {s['bonus']:+d},心态 {s['mood']:+d})")
        while True:
            raw = input("选策略(1/2/3 或名称):").strip()
            key = STRATEGY_ALIASES.get(raw)
            if key is None:
                print("看不懂,重选一个(1=蹲点,2=掐点,3=佛系)。")
                continue
            break
        rec = resolve_day(state, day, key)
        print(describe_day(rec))
        time.sleep(0.4)
    name, blurb = title_for(state.meals)
    print("\n" + "=" * 42)
    print(f"7 天结束!你抢到了 {state.meals} 顿机关食堂午餐。")
    print(f"称号:{name}")
    print(f"评语:{blurb}")
    print("=" * 42)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="机关食堂抢位模拟器")
    ap.add_argument("--auto", action="store_true", help="自动演示,无需交互")
    ap.add_argument("--games", type=int, default=1, help="自动演示局数")
    ap.add_argument("--seed", type=int, default=None, help="随机种子")
    ap.add_argument("--verbose", action="store_true", help="打印每日战报")
    args = ap.parse_args(argv)

    if args.auto:
        if args.games < 1:
            ap.error("--games 必须 >= 1")
        base = args.seed if args.seed is not None else random.randrange(2 ** 31)
        results = []
        for g in range(args.games):
            state = auto_game(base + g)
            results.append(state)
            if args.verbose:
                print(f"[第 {g + 1} 局 seed={base + g}]")
                for rec in state.days:
                    print(describe_day(rec))
                name, _ = title_for(state.meals)
                print(f"  => {state.meals} 顿,称号:{name}\n")
        print_summary(results)
        return 0
    return interactive()


if __name__ == "__main__":
    sys.exit(main())
