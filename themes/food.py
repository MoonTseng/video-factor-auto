"""日本美食主题 — 拉面/天妇罗/寿司/居酒屋 原声+字幕

特点：
- 保留原声（ASMR 感很重要）
- 食物名称字幕
- 美食出锅/特写帧做封面
- 分区: 211=美食制作 或 212=美食侦探
"""

import os
import random
from themes.base import BaseTheme


class FoodTheme(BaseTheme):
    name = "food"
    tid = 211            # 美食制作
    audio_mode = "subtitle_only"
    copyright = 2

    SEARCH_QUERIES = [
        "ラーメン 職人 密着",
        "天ぷら 職人",
        "寿司 職人 密着",
        "蕎麦 職人 こだわり",
        "うどん 讃岐 手打ち",
        "焼き鳥 職人",
        "屋台 ラーメン 深夜",
        "日本 食堂 ドキュメンタリー",
        "おばあちゃん 料理",
        "日本 夫婦 料理",
        "築地 朝ごはん",
        "日本 居酒屋 路地裏",
        "和食 ドキュメンタリー",
        "たこ焼き 屋台",
        "お好み焼き 大阪",
        "日本 カレー 名店",
        "おでん 屋台 冬",
        "日本 パン屋 早朝",
        "とんかつ 名店 行列",
        "日本 定食屋 密着",
    ]

    # 食物中日对照
    FOOD_NAMES = {
        "ラーメン": "拉面", "天ぷら": "天妇罗", "寿司": "寿司",
        "蕎麦": "荞麦面", "うどん": "乌冬面", "焼き鳥": "烤鸡串",
        "たこ焼き": "章鱼烧", "お好み焼き": "大阪烧",
        "おでん": "关东煮", "カレー": "咖喱", "とんかつ": "炸猪排",
        "定食": "定食", "パン": "面包", "餃子": "饺子",
        "焼肉": "烤肉", "鰻": "鳗鱼", "抹茶": "抹茶",
    }

    def get_search_queries(self, keyword: str = "") -> list[str]:
        if keyword:
            return [
                f"日本 {keyword} 職人",
                f"日本 {keyword} 密着",
                f"日本 {keyword} ドキュメンタリー",
                f"{keyword} 名店 日本",
                f"Japan {keyword} street food",
            ]
        return self.SEARCH_QUERIES

    # ── 标题公式：日本原声美食|食物名+治愈瞬间 ──
    TITLE_STYLES = [
        "日本原声美食｜{food}の匠人精神，看完太治愈了",
        "🍜 {food}！日本街头美食原声记录｜治愈向",
        "【日本美食】{food}的极致匠心｜原声无解说",
        "日本{food}｜职人手艺的治愈瞬间🔥原声记录",
        "太治愈了！日本{food}制作全过程｜原声ASMR",
        "🇯🇵 日本街头{food}｜看完想马上飞日本",
    ]

    def generate_title(self, video_info: dict) -> str:
        title = video_info.get("title", "")
        food = self._detect_food(title) or "美食"

        template = random.choice(self.TITLE_STYLES)
        result = template.format(food=food)

        if len(result) > 80:
            result = f"日本原声美食｜{food}的治愈瞬间"
        return result[:80]

    def generate_desc(self, video_info: dict) -> str:
        """用 LLM 生成内容简介 + hashtag"""
        title = video_info.get("title", "")
        food = self._detect_food(title) or "美食"
        transcript_preview = video_info.get("transcript_preview", "")

        try:
            from writer import _call_llm
            import yaml
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            transcript_hint = ""
            if transcript_preview:
                transcript_hint = f"\n视频旁白/解说（供参考，不要照抄）:\n{transcript_preview[:500]}\n"

            prompt = f"""为一个 B站美食视频写简介。这是一个日本{food}的制作/探店视频。

原始标题: {title}
{transcript_hint}
要求:
1. 用中文简要介绍视频内容（这道美食的特色、匠人精神、制作亮点等，2-3句话），要有具体内容
2. 不要出现"搬运自"、"来自"、"转载"、"源自"、"原视频"、"二创"、"品牌化"等字样
3. 不要出现任何URL链接
4. 不要解释字幕翻译、方便观看之类的说明性文字
5. 语气自然，像一个美食博主在安利
5. 最后加上一行 hashtag 标签（用 # 开头），包含: #日本美食 #美食 #{food} 以及2-3个吸引人的标签
6. 整体不超过200字

直接输出简介文本，不要加任何前缀。"""

            desc = _call_llm(
                config,
                [{"role": "user", "content": prompt}],
                max_tokens=400,
            ).strip()

            for bad_word in ["搬运", "转载", "来源", "原视频", "二创", "品牌化", "中文字幕版", "方便观看", "http", "youtube", "youtu.be"]:
                if bad_word.lower() in desc.lower():
                    raise ValueError(f"LLM 生成的简介包含禁止词: {bad_word}")

            return desc

        except Exception:
            lines = [
                f"🍽️ 日本{food}的匠人制作过程",
                "",
                f"看日本{food}职人如何用双手创造美味，每一步都是匠心。",
                "保留原声，感受最真实的日本街头美食氛围～",
                "",
                "🔔 关注UP主，每天带你看日本治愈系美食",
                "👍 喜欢请三连支持！",
                "",
                f"#日本美食 #美食 #{food} #治愈 #匠人精神 #街头美食",
            ]
            return "\n".join(lines)

    def generate_tags(self, video_info: dict) -> list[str]:
        title = video_info.get("title", "")
        food = self._detect_food(title)

        tags = ["日本美食", "美食", "治愈", "日本", "原声", "匠人", "街头美食"]
        if food and food not in tags:
            tags.insert(0, food)
        tags.extend(["日本料理", "美食纪录片", "ASMR"])
        # 去重
        seen = set()
        result = []
        for t in tags:
            if t not in seen:
                seen.add(t)
                result.append(t[:20])
        return result[:10]

    def generate_title_douyin(self, video_info: dict) -> str:
        """抖音标题 — 30字以内，美食向"""
        title = video_info.get("title", "")
        food = self._detect_food(title) or "美食"

        templates = [
            f"🍜日本{food}！看完太治愈了",
            f"日本街头{food}，这手艺绝了🔥",
            f"治愈！日本{food}制作全过程",
            f"🇯🇵日本{food}职人的匠心时刻",
        ]

        templates.sort(key=len, reverse=True)
        for t in templates:
            if len(t) <= 30:
                return t
        return templates[-1][:30]

    def generate_desc_douyin(self, video_info: dict) -> str:
        """抖音专用简介 — 美食向短描述"""
        title = video_info.get("title", "")
        food = self._detect_food(title) or "美食"

        try:
            from writer import _call_llm
            import yaml
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            prompt = f"""为一个抖音美食短视频写描述。这是日本{food}的制作/探店视频，保留原声。

原始标题: {title}

要求:
1. 2-3句话介绍这道美食的特色和看点，语气像美食博主安利
2. 不要出现"搬运"、"转载"、"来自"等字样，不要URL
3. 不要出现"UP主"、"三连"、"B站"等其他平台用语
4. 最后一行话题标签：#日本美食 #{food} 加3个相关热门话题
5. 总字数控制在120字以内

直接输出描述。"""

            desc = _call_llm(
                config,
                [{"role": "user", "content": prompt}],
                max_tokens=250,
            ).strip()

            for bad_word in ["搬运", "转载", "来源", "原视频", "http", "youtube", "UP主", "三连", "B站"]:
                if bad_word.lower() in desc.lower():
                    raise ValueError(f"包含禁止词: {bad_word}")

            return desc[:1000]

        except Exception:
            return (
                f"🍽️ 日本{food}职人的治愈手艺，每一步都是匠心\n"
                f"保留原声感受最真实的日本美食氛围～\n\n"
                f"#日本美食 #{food} #治愈 #匠人精神 #街头美食"
            )

    def get_cover_strategy(self) -> dict:
        return {
            "method": "scene_detect",
            "prefer": "food_closeup",   # 食物特写/出锅瞬间
            "crop_black_bars": True,
            "target_ratio": "16:9",
            "skip_first_seconds": 5,
            "skip_last_seconds": 5,
        }

    def get_translate_prompt(self) -> str:
        return "日本美食纪录片，保留食物日文名，括号加中文翻译"

    def get_whisper_override(self) -> dict:
        return {"language": "ja"}  # 强制日语

    def _detect_food(self, title: str) -> str:
        """从标题检测食物名"""
        for ja, zh in self.FOOD_NAMES.items():
            if ja in title:
                return zh
        # 英文 fallback
        en_map = {
            "ramen": "拉面", "sushi": "寿司", "tempura": "天妇罗",
            "udon": "乌冬", "soba": "荞麦面", "takoyaki": "章鱼烧",
            "curry": "咖喱", "tonkatsu": "炸猪排",
        }
        lower = title.lower()
        for en, zh in en_map.items():
            if en in lower:
                return zh
        return ""
