"""Netflix/Hulu/Disney 日韩预告片主题

特点：
- 保留原声 + 中文字幕
- 高清画质优先
- 悬疑/冲突帧做封面
- 分区: 183=影视剪辑
"""

import os
import random
import re
from themes.base import BaseTheme


class NetflixTheme(BaseTheme):
    name = "netflix"
    tid = 183            # 影视剪辑
    audio_mode = "subtitle_only"
    copyright = 2        # 转载

    # ── 搜索关键词 ──
    PLATFORM_QUERIES = {
        "netflix_japan": [
            "Netflix Japan {year} 予告",
            "Netflix 日本 {year} 新作 予告編",
            "Netflix Japan {year} trailer",
            "Netflix 日本映画 {year} 予告",
        ],
        "netflix_korea": [
            "Netflix Korea {year} 예고편",
            "넷플릭스 {year} 한국 드라마 예고",
            "Netflix Korean drama {year} trailer",
        ],
        "disney_japan": [
            "Disney+ Japan {year} 予告",
            "ディズニープラス {year} 新作 予告編",
        ],
        "hulu_japan": [
            "Hulu Japan {year} 予告",
            "Hulu ジャパン 新作ドラマ {year}",
        ],
    }

    def get_search_queries(self, keyword: str = "") -> list[str]:
        from datetime import datetime
        year = datetime.now().year

        if keyword:
            # 用户指定了关键词，直接用
            return [
                f"{keyword} trailer {year}",
                f"{keyword} 予告 {year}",
                f"{keyword} Netflix trailer",
                f"{keyword} 予告編",
            ]

        queries = []
        for platform, templates in self.PLATFORM_QUERIES.items():
            for t in templates:
                queries.append(t.format(year=year))
        return queries

    # ── 标题公式 ──
    TITLE_PREFIXES = [
        "🔥", "终于来了！", "【重磅】", "震撼！", "必看！",
        "💥", "高能预警！", "炸裂！", "神作预定！",
    ]

    def generate_title(self, video_info: dict) -> str:
        original = video_info.get("title", "")
        platform = video_info.get("platform", "")
        work_name = self._extract_work_name(original)

        platform_cn = {
            "netflix_japan": "Netflix日本",
            "netflix_korea": "Netflix韩国",
            "disney_japan": "Disney+",
            "hulu_japan": "Hulu日本",
        }.get(platform, "Netflix")

        prefix = random.choice(self.TITLE_PREFIXES)

        templates = [
            f"{prefix}{platform_cn}《{work_name}》官方预告｜中文字幕",
            f"【{platform_cn}】{work_name}｜重磅新作预告！中字",
            f"{prefix}{platform_cn} 新作《{work_name}》预告片【中字】",
            f"【中字预告】{work_name}丨{platform_cn}最新力作",
        ]

        # 选最长但不超80字的
        templates.sort(key=len, reverse=True)
        for t in templates:
            if len(t) <= 80:
                return t
        return templates[-1][:80]

    # ── 简介 ──
    def generate_desc(self, video_info: dict) -> str:
        """用 LLM 生成内容简介 + hashtag，不出现搬运/来自/转载等字样"""
        original = video_info.get("title", "")
        platform = video_info.get("platform", "")
        transcript_preview = video_info.get("transcript_preview", "")

        platform_cn = {
            "netflix_japan": "Netflix",
            "netflix_korea": "Netflix",
            "disney_japan": "Disney+",
            "hulu_japan": "Hulu",
        }.get(platform, "Netflix")

        work_name = self._extract_work_name(original)

        # 尝试用 LLM 生成更好的简介
        try:
            from writer import _call_llm
            import yaml
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            transcript_hint = ""
            if transcript_preview:
                transcript_hint = f"\n视频台词/旁白（供参考，不要照抄）:\n{transcript_preview[:500]}\n"

            prompt = f"""为一个 B站视频写简介。这是一部{platform_cn}平台的日剧/电影预告片，作品名《{work_name}》。

原始标题: {original}
{transcript_hint}
要求:
1. 用中文简要介绍这部作品的类型、看点、剧情概要(2-3句话)，要有具体内容而非空话套话
2. 不要出现"搬运自"、"来自"、"转载"、"源自"、"原视频"等字样
3. 不要出现任何URL链接
4. 语气自然，像一个追剧博主在安利作品
5. 最后加上一行 hashtag 标签（用 # 开头），包含: #{platform_cn} #预告片 #日剧 以及2-3个跟作品内容相关的吸引人的标签
6. 整体不超过200字

直接输出简介文本，不要加任何前缀。"""

            desc = _call_llm(
                config,
                [{"role": "user", "content": prompt}],
                max_tokens=400,
            ).strip()

            # 确保没有被 LLM 加上 "搬运" 等关键词
            for bad_word in ["搬运", "转载", "来源", "原视频", "http", "youtube", "youtu.be"]:
                if bad_word.lower() in desc.lower():
                    raise ValueError(f"LLM 生成的简介包含禁止词: {bad_word}")

            return desc

        except Exception:
            # LLM 失败时用模板 fallback（也不出现搬运字样）
            lines = [
                f"📺 {platform_cn}新作《{work_name}》预告片",
                "",
                "期待已久的新作终于来了！中文字幕版预告，先睹为快～",
                "字幕翻译仅供参考，欢迎指正！",
                "",
                "💡 更多日韩影视预告请关注UP主",
                "🔔 一键三连，第一时间获取最新预告！",
                "",
                f"#{platform_cn} #预告片 #日剧 #追剧 #新剧推荐",
            ]
            return "\n".join(lines)

    # ── 标签 ──
    def generate_tags(self, video_info: dict) -> list[str]:
        platform = video_info.get("platform", "")
        base_tags = ["预告片", "中文字幕", "追剧", "新剧推荐"]

        platform_tags = {
            "netflix_japan": ["Netflix", "日剧", "奈飞", "日本Netflix", "电视剧"],
            "netflix_korea": ["Netflix", "韩剧", "奈飞", "韩国Netflix", "电视剧"],
            "disney_japan": ["Disney+", "迪士尼", "日剧", "电视剧"],
            "hulu_japan": ["Hulu", "日剧", "电视剧"],
        }
        extra = platform_tags.get(platform, ["Netflix", "日剧", "电视剧"])

        # 从标题提取作品名加入 tag
        work_name = self._extract_work_name(video_info.get("title", ""))
        if work_name and len(work_name) <= 20:
            base_tags.insert(0, work_name)

        all_tags = base_tags + extra
        # 去重保序
        seen = set()
        result = []
        for t in all_tags:
            if t not in seen:
                seen.add(t)
                result.append(t[:20])
        return result[:10]

    def generate_desc_douyin(self, video_info: dict) -> str:
        """抖音专用简介 — 短平快 + 话题标签"""
        original = video_info.get("title", "")
        platform = video_info.get("platform", "")
        work_name = self._extract_work_name(original)

        platform_cn = {
            "netflix_japan": "Netflix",
            "netflix_korea": "Netflix",
            "disney_japan": "Disney+",
            "hulu_japan": "Hulu",
        }.get(platform, "Netflix")

        try:
            from writer import _call_llm
            import yaml
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            prompt = f"""为一个抖音短视频写描述。这是{platform_cn}平台的日剧/电影《{work_name}》预告片，带中文字幕。

原始标题: {original}

要求:
1. 2-3句话介绍作品看点，语气像追剧博主安利，要有吸引力
2. 绝对不要出现"搬运"、"转载"、"来自"、"源自"、"原视频"等字样
3. 不要出现任何URL链接
4. 不要出现"UP主"、"三连"、"B站"等其他平台用语
5. 最后一行放话题标签，用 # 开头，5个左右：#{platform_cn} #预告片 #日剧 加2个跟内容相关的
6. 总字数控制在150字以内，精炼有力

直接输出描述文本。"""

            desc = _call_llm(
                config,
                [{"role": "user", "content": prompt}],
                max_tokens=300,
            ).strip()

            for bad_word in ["搬运", "转载", "来源", "原视频", "http", "youtube", "youtu.be", "UP主", "三连", "B站"]:
                if bad_word.lower() in desc.lower():
                    raise ValueError(f"包含禁止词: {bad_word}")

            return desc[:1000]

        except Exception:
            return (
                f"📺 {platform_cn}新作《{work_name}》预告来了！中文字幕版先睹为快\n"
                f"这部新作太期待了，看完预告直接列入必追清单！\n\n"
                f"#{platform_cn} #预告片 #日剧 #追剧 #新剧推荐"
            )

    def generate_title_douyin(self, video_info: dict) -> str:
        """抖音标题 — 30字以内，直击看点"""
        original = video_info.get("title", "")
        platform = video_info.get("platform", "")
        work_name = self._extract_work_name(original)

        platform_cn = {
            "netflix_japan": "Netflix",
            "netflix_korea": "Netflix",
            "disney_japan": "Disney+",
            "hulu_japan": "Hulu",
        }.get(platform, "Netflix")

        templates = [
            f"🔥{platform_cn}《{work_name}》预告来了",
            f"《{work_name}》预告！{platform_cn}新作",
            f"💥{platform_cn}新作《{work_name}》",
            f"终于来了！《{work_name}》预告片",
        ]

        # 选最长但不超30字的
        templates.sort(key=len, reverse=True)
        for t in templates:
            if len(t) <= 30:
                return t
        return templates[-1][:30]

    # ── 封面策略 ──
    def get_cover_strategy(self) -> dict:
        return {
            "method": "scene_detect",
            "prefer": "high_contrast",  # 悬疑冲突画面
            "crop_black_bars": True,
            "target_ratio": "16:9",
            "skip_first_seconds": 2,    # 跳过片头黑屏
            "skip_last_seconds": 3,     # 跳过片尾 logo
        }

    def get_translate_prompt(self) -> str:
        return "影视预告片，需要保持紧张悬疑的语气"

    # ── 工具 ──
    def _extract_work_name(self, title: str) -> str:
        """从 YouTube 标题提取作品名"""
        parts = re.split(r'\s*[\|｜\-\—]\s*', title)
        noise = [
            r'(?i)official\s*(trailer|teaser)',
            r'(?i)^(trailer|teaser)',
            r'(?i)^netflix\b', r'(?i)^disney\+?', r'(?i)^hulu\b',
            r'予告(編)?', r'ティーザー', r'예고편',
            r'^넷플릭스', r'^\d{4}$',
        ]
        candidates = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            is_noise = any(re.search(p, part) for p in noise)
            if not is_noise:
                candidates.append(part)

        if candidates:
            return max(candidates, key=len)
        # fallback: 清理原标题
        cleaned = re.sub(r'(?i)(official\s*)?(trailer|teaser|予告編?|예고편?)', '', title)
        cleaned = re.sub(r'(?i)(netflix|disney\+?|hulu)\s*(japan|korea|日本|韓国)?\s*', '', cleaned)
        return cleaned.strip()[:30] or title[:30]
