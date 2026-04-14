"""日本隐藏旅行主题 — 镰仓/京都小巷/北海道乡下/九州温泉

特点：
- 保留原声（环境音很重要）
- 地点名称字幕
- 绝美风景帧做封面
- 分区: 250=旅行出行
"""

import os
import random
from themes.base import BaseTheme


class TravelTheme(BaseTheme):
    name = "travel"
    tid = 250            # 旅行出行
    audio_mode = "subtitle_only"
    copyright = 2

    SEARCH_QUERIES = [
        # 铁道旅行（治愈系爆款）
        "日本 鉄道 車窓 旅",
        "日本 電車 海沿い 絶景",
        "日本 ローカル線 旅",
        "江ノ電 車窓",
        "日本 列車 雪景色",
        "五能線 リゾートしらかみ 車窓",
        "日本 鉄道 癒し",
        "青春18きっぷ 旅",
        "日本 路面電車 旅",
        "箱根登山鉄道 車窓",
        # 散步/旅行
        "日本 田舎 旅",
        "一人旅 日本",
        "日本の小さな町",
        "日本 温泉 旅館",
        "日本 離島 旅",
        "日本 商店街 散歩",
        "鎌倉 散歩 vlog",
        "京都 路地裏 散策",
        "北海道 田舎 ドライブ",
        "九州 温泉 旅行",
        "奈良 鹿 散歩",
        "日本 雪景色 冬",
        "日本 桜 散歩",
        "日本 紅葉 旅",
        "尾道 坂道 散歩",
        "日本 古い町並み",
        "日本 海辺 散歩",
        "日本 山 登山 絶景",
        "四国 旅 88",
        "沖縄 離島 旅",
    ]

    # 铁道线路中日对照
    TRAIN_NAMES = {
        "江ノ電": "江之电", "江ノ島電鉄": "江之岛电铁",
        "五能線": "五能线", "只見線": "只见线",
        "箱根登山": "箱根登山铁道", "嵯峨野": "�的嵯峨野",
        "しらかみ": "白神号", "ゆふいんの森": "由布院之森",
        "SL": "蒸汽机车", "新幹線": "新干线",
        "路面電車": "路面电车", "ローカル線": "地方线",
        "鉄道": "铁道", "電車": "电车", "列車": "列车",
        "車窓": "车窗风景",
    }

    # 地点中日对照
    PLACE_NAMES = {
        "鎌倉": "镰仓", "京都": "京都", "北海道": "北海道",
        "九州": "九州", "奈良": "奈良", "沖縄": "冲绳",
        "尾道": "尾道", "四国": "四国", "箱根": "箱根",
        "軽井沢": "轻井泽", "金沢": "金泽", "高山": "高山",
        "白川郷": "白川乡", "富士": "富士", "屋久島": "屋久岛",
        "直島": "直岛", "小豆島": "小豆岛", "函館": "函馆",
        "札幌": "札幌", "熊本": "熊本", "長崎": "长崎",
        "広島": "广岛", "松本": "松本", "日光": "日光",
        "江ノ島": "江之岛", "湘南": "湘南",
    }

    SEASON_WORDS = {
        "桜": "樱花季", "紅葉": "红叶季", "雪": "冬雪",
        "夏": "盛夏", "春": "早春", "秋": "深秋",
    }

    def get_search_queries(self, keyword: str = "") -> list[str]:
        if keyword:
            return [
                f"日本 {keyword} 旅",
                f"日本 {keyword} 散歩",
                f"{keyword} vlog Japan",
                f"日本 {keyword} 絶景",
                f"Japan {keyword} travel",
            ]
        return self.SEARCH_QUERIES

    # ── 标题公式：日本旅行原声|地点+季节 ──
    TITLE_STYLES = [
        "日本旅行原声｜{place}的{season}太美了，想立刻出发",
        "🇯🇵 {place}{season}漫步｜日本原声旅行记录",
        "【日本旅行】{place}隐藏秘境｜{season}原声散步",
        "太美了！{place}的{season}｜日本原声旅行VLOG",
        "日本{place}｜{season}一个人的治愈旅行🌸",
        "这才是真正的日本！{place}{season}原声漫游",
    ]

    def generate_title(self, video_info: dict) -> str:
        title = video_info.get("title", "")
        place = self._detect_place(title) or "小镇"
        season = self._detect_season(title) or "秘境"

        template = random.choice(self.TITLE_STYLES)
        result = template.format(place=place, season=season)

        if len(result) > 80:
            result = f"日本旅行原声｜{place}的{season}"
        return result[:80]

    def generate_desc(self, video_info: dict) -> str:
        """用 LLM 生成内容简介 + hashtag"""
        title = video_info.get("title", "")
        place = self._detect_place(title) or "日本"
        season = self._detect_season(title) or ""

        try:
            from writer import _call_llm
            import yaml
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            season_hint = f"，{season}时节" if season else ""
            prompt = f"""为一个 B站旅行视频写简介。这是一个日本{place}{season_hint}的旅行散步视频。

原始标题: {title}

要求:
1. 用中文简要介绍这个地方的特色和看点（风景、氛围、文化特色等，2-3句话）
2. 不要出现"搬运自"、"来自"、"转载"、"源自"、"原视频"等字样
3. 不要出现任何URL链接
4. 语气自然，像一个旅行博主在分享见闻
5. 最后加上一行 hashtag 标签（用 # 开头），包含: #日本旅行 #日本 #{place} 以及2-3个吸引人的标签
6. 整体不超过200字

直接输出简介文本，不要加任何前缀。"""

            desc = _call_llm(
                config,
                [{"role": "user", "content": prompt}],
                max_tokens=400,
            ).strip()

            for bad_word in ["搬运", "转载", "来源", "原视频", "http", "youtube", "youtu.be"]:
                if bad_word.lower() in desc.lower():
                    raise ValueError(f"LLM 生成的简介包含禁止词: {bad_word}")

            return desc

        except Exception:
            lines = [
                f"🗾 {place}的原声旅行记录",
                "",
                f"跟随镜头漫步{place}，发现不一样的日本风景。",
                "保留环境原声，感受最真实的日本～",
                "",
                "🔔 关注UP主，每天带你云游日本隐藏秘境",
                "👍 想去日本的请扣1！三连支持！",
                "",
                f"#日本旅行 #日本 #{place} #治愈 #散步 #秘境",
            ]
            return "\n".join(lines)

    def generate_tags(self, video_info: dict) -> list[str]:
        title = video_info.get("title", "")
        place = self._detect_place(title)
        season = self._detect_season(title)

        tags = ["日本旅行", "旅行", "日本", "原声", "散步", "治愈"]
        if place and place not in tags:
            tags.insert(0, place)
        if season and season not in tags:
            tags.insert(1, season)
        tags.extend(["vlog", "日本旅游", "秘境", "一个人旅行"])
        # 去重
        seen = set()
        result = []
        for t in tags:
            if t not in seen:
                seen.add(t)
                result.append(t[:20])
        return result[:10]

    def generate_title_douyin(self, video_info: dict) -> str:
        """抖音标题 — 30字以内，旅行向"""
        title = video_info.get("title", "")
        place = self._detect_place(title) or "小镇"
        season = self._detect_season(title) or "秘境"

        templates = [
            f"🇯🇵日本{place}{season}太美了！",
            f"治愈！日本{place}的{season}漫步",
            f"日本{place}｜{season}原声散步🌸",
            f"这才是真正的日本！{place}{season}",
        ]

        templates.sort(key=len, reverse=True)
        for t in templates:
            if len(t) <= 30:
                return t
        return templates[-1][:30]

    def generate_desc_douyin(self, video_info: dict) -> str:
        """抖音专用简介 — 旅行向短描述"""
        title = video_info.get("title", "")
        place = self._detect_place(title) or "日本"
        season = self._detect_season(title) or ""

        try:
            from writer import _call_llm
            import yaml
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            season_hint = f"，{season}时节" if season else ""
            prompt = f"""为一个抖音旅行短视频写描述。这是日本{place}{season_hint}的旅行散步视频，保留环境原声。

原始标题: {title}

要求:
1. 2-3句话介绍这个地方的看点和氛围，语气像旅行博主分享
2. 不要出现"搬运"、"转载"、"来自"等字样，不要URL
3. 不要出现"UP主"、"三连"、"B站"等其他平台用语
4. 最后一行话题标签：#日本旅行 #{place} 加3个相关热门话题
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
                f"🗾 日本{place}原声漫步，感受最真实的日本风景\n"
                f"跟随镜头发现不一样的日本～\n\n"
                f"#日本旅行 #{place} #治愈 #散步 #日本"
            )

    def get_cover_strategy(self) -> dict:
        return {
            "method": "scene_detect",
            "prefer": "landscape",      # 绝美风景
            "crop_black_bars": True,
            "target_ratio": "16:9",
            "skip_first_seconds": 3,
            "skip_last_seconds": 5,
        }

    def get_translate_prompt(self) -> str:
        return "日本旅行视频，保留地名日文原名，括号加中文翻译"

    def get_whisper_override(self) -> dict:
        return {"language": "ja"}

    def _detect_place(self, title: str) -> str:
        for ja, zh in self.PLACE_NAMES.items():
            if ja in title:
                return zh
        en_map = {
            "kamakura": "镰仓", "kyoto": "京都", "hokkaido": "北海道",
            "okinawa": "冲绳", "nara": "奈良", "hakone": "箱根",
            "tokyo": "东京", "osaka": "大阪", "fuji": "富士",
        }
        lower = title.lower()
        for en, zh in en_map.items():
            if en in lower:
                return zh
        return ""

    def _detect_season(self, title: str) -> str:
        for ja, zh in self.SEASON_WORDS.items():
            if ja in title:
                return zh
        return ""
