"""三大主题模板 — Netflix预告/日本美食/日本旅行

每个主题定义：
- 搜索关键词 (YouTube)
- 标题公式
- 简介模板
- 标签集
- 封面策略
- B站分区
- 音频模式
"""

from themes.netflix import NetflixTheme
from themes.food import FoodTheme
from themes.travel import TravelTheme

THEMES = {
    "netflix": NetflixTheme,
    "food": FoodTheme,
    "travel": TravelTheme,
}


def get_theme(name: str):
    """获取主题实例"""
    cls = THEMES.get(name)
    if not cls:
        raise ValueError(f"未知主题: {name}，可选: {', '.join(THEMES.keys())}")
    return cls()


def list_themes() -> list[str]:
    return list(THEMES.keys())
