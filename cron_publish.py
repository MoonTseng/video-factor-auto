#!/usr/bin/env python3
"""
定时视频发布脚本 — 供 cron 调用
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

轮转模式: 预告片 → 旅行 → 旅行 → 美食 (4-slot cycle)
时段: 10:00 - 18:00, 每 2 小时一次 (5 slots/天)
日期内序号决定主题: slot_index % 4 → 0:netflix, 1:travel, 2:travel, 3:food

用法:
  python cron_publish.py                 # 自动根据当前时间选主题
  python cron_publish.py --theme netflix # 手动指定主题
  python cron_publish.py --dry-run       # 只显示将要执行的操作
"""

import argparse
import json
import os
import sys
from datetime import datetime

# 确保工作目录正确
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 轮转表: 索引 % 4 决定主题
ROTATION = ["netflix", "travel", "travel", "food"]

# 每日时段 (小时)
SLOTS = [10, 12, 14, 16, 18]

# 状态文件: 记录当天已发布的 slot 计数
STATE_FILE = os.path.join(os.path.dirname(__file__), ".cron_state.json")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_today_slot_index() -> int:
    """获取今天已发布到第几个 slot（用于轮转）"""
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("date") != today:
        # 新的一天，重置
        return 0
    return state.get("slot_index", 0)


def advance_slot():
    """推进 slot 计数"""
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("date") != today:
        state = {"date": today, "slot_index": 1}
    else:
        state["slot_index"] = state.get("slot_index", 0) + 1
    state["last_run"] = datetime.now().isoformat()
    save_state(state)


def get_theme_for_now() -> str:
    """根据当天 slot 索引决定主题"""
    idx = get_today_slot_index()
    theme = ROTATION[idx % len(ROTATION)]
    return theme


def main():
    parser = argparse.ArgumentParser(description="定时视频发布")
    parser.add_argument("--theme", choices=["netflix", "travel", "food"],
                        help="手动指定主题（覆盖轮转）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只显示将要执行的操作")
    parser.add_argument("--platforms", default="bilibili",
                        help="发布平台，逗号分隔 (bilibili,douyin)")
    args = parser.parse_args()

    now = datetime.now()
    hour = now.hour

    # 检查是否在时段内
    if hour < 10 or hour > 18:
        print(f"⏰ 当前 {now.strftime('%H:%M')}，不在发布时段 (10:00-18:00)，跳过")
        return

    # 确定主题
    theme = args.theme or get_theme_for_now()
    slot_idx = get_today_slot_index()
    platforms = [p.strip() for p in args.platforms.split(",")]

    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"🕐 时间: {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"🎬 主题: {theme} (slot #{slot_idx}, 轮转: {ROTATION})")
    print(f"📡 平台: {', '.join(platforms)}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if args.dry_run:
        print("🏃 DRY RUN — 不实际执行")
        return

    # 执行管线
    try:
        from main import load_config, run_pipeline
        config = load_config()
        result = run_pipeline(config, theme, platforms=platforms)

        if result:
            print(f"\n✅ 发布成功: {result}")
            advance_slot()
        else:
            print(f"\n❌ 发布失败（无可用视频或管线出错）")
            # 失败也推进 slot，避免卡在同一主题
            advance_slot()

    except Exception as e:
        print(f"\n💥 异常: {e}")
        import traceback
        traceback.print_exc()
        # 异常也推进
        advance_slot()
        sys.exit(1)


if __name__ == "__main__":
    main()
