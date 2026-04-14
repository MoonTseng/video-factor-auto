"""基础主题类 — 所有主题继承此类"""

import random
from abc import ABC, abstractmethod


class BaseTheme(ABC):
    """主题基类"""

    name: str = ""
    tid: int = 183          # B站分区 ID
    audio_mode: str = "subtitle_only"  # subtitle_only / original / dubbed
    copyright: int = 2      # 1=原创 2=转载

    # ── 搜索 ──
    @abstractmethod
    def get_search_queries(self, keyword: str = "") -> list[str]:
        """返回 YouTube 搜索关键词列表"""
        ...

    # ── 标题 ──
    @abstractmethod
    def generate_title(self, video_info: dict) -> str:
        """生成B站标题 (<=80字)"""
        ...

    # ── 简介 ──
    @abstractmethod
    def generate_desc(self, video_info: dict) -> str:
        """生成B站简介 (<=2000字)"""
        ...

    # ── 标签 ──
    @abstractmethod
    def generate_tags(self, video_info: dict) -> list[str]:
        """生成B站标签 (<=10个, 每个<=20字)"""
        ...

    # ── 抖音适配（默认从B站版本转换，子类可覆盖） ──

    def generate_title_douyin(self, video_info: dict) -> str:
        """生成抖音标题 (<=30字，精炼吸引人)"""
        bili_title = self.generate_title(video_info)
        # 去掉B站常用的【】前缀和｜后缀，精炼到30字
        import re
        short = re.sub(r'【[^】]*】\s*', '', bili_title)
        short = re.sub(r'\s*[｜|].*$', '', short)
        short = short.strip()
        if len(short) <= 30:
            return short
        # 还是太长，截断到30字
        return short[:29] + "…"

    def generate_desc_douyin(self, video_info: dict) -> str:
        """生成抖音描述 (<=1000字，话题标签用#开头)"""
        # 默认用B站简介，去掉B站特有的内容
        import re
        desc = self.generate_desc(video_info)
        # 去掉"三连"、"UP主"等B站特有用语
        desc = re.sub(r'.*三连.*\n?', '', desc)
        desc = re.sub(r'.*UP主.*\n?', '', desc)
        desc = re.sub(r'.*关注.*UP.*\n?', '', desc)
        # 限制1000字
        return desc.strip()[:1000]

    def generate_tags_douyin(self, video_info: dict) -> list[str]:
        """生成抖音话题标签 (<=5个，热门话题优先)"""
        tags = self.generate_tags(video_info)
        # 抖音话题不宜太多，取前5个
        return tags[:5]

    # ── 封面策略 ──
    def get_cover_strategy(self) -> dict:
        """返回封面提取策略"""
        return {
            "method": "scene_detect",   # scene_detect / interval / specific_time
            "prefer": "high_contrast",  # high_contrast / face / food / landscape
            "crop_black_bars": True,
            "target_ratio": "16:9",
        }

    # ── 翻译 prompt ──
    def get_translate_prompt(self) -> str:
        """返回翻译提示词的风格描述"""
        return "影视预告片"

    # ── Whisper 配置覆盖 ──
    def get_whisper_override(self) -> dict:
        """覆盖 whisper 配置（如语言检测）"""
        return {}  # 空=用默认配置
