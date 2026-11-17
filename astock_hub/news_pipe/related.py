# -*- coding: utf-8 -*-
"""个股 → 产业资讯 12 赛道映射（信息面"相关产业动态"用，2026-10-26）。

为什么按行业/概念而不是股票名：产业资讯是全球英文产业源翻译，正文几乎不出现
A 股公司简称（按"移远通信"搜常年零命中）；公司的同花顺行业/概念名与赛道
关键词同源同语言，命中率高。

匹配规则：行业名命中关键词计 2 分，每个概念名命中计 1 分（同赛道累加）；
0 分不入选，按分数降序、同分按 TRACKS 定义顺序，最多取 3 个赛道。
science 赛道只接前沿概念（量子/脑机接口等），不做行业映射。
"""
from __future__ import annotations

# key 与 news.json industries[].key / news_pipe.builder.INDUSTRIES 一致
TRACKS: dict[str, tuple[str, tuple[str, ...]]] = {
    "ai": ("AI / 大模型", (
        "人工智能", "大模型", "算力", "CPO", "光模块", "液冷", "数据中心",
        "AIGC", "东数西算", "英伟达", "昇腾", "DeepSeek", "多模态",
        "AI应用", "智能体", "AI眼镜", "AI手机", "AI PC", "智能算力",
        "AI服务器", "智算")),
    "semi": ("半导体 / 芯片", (
        "半导体", "芯片", "集成电路", "光刻", "存储", "先进封装", "封装测试",
        "PCB", "电子元件", "第三代半导体", "碳化硅", "IGBT", "GPU", "晶圆",
        "Chiplet", "EDA", "半导体材料", "半导体设备", "分立器件", "模拟芯片")),
    "robot": ("机器人 / 自动化", (
        "机器人", "减速器", "自动化", "机床", "伺服", "机器视觉",
        "工业互联网", "工业母机", "传感器", "人形机器人", "灵巧手")),
    "auto": ("汽车 / 新能源车", (
        "汽车", "新能源车", "智能驾驶", "无人驾驶", "充电桩", "整车",
        "车载", "线控", "换电", "华为汽车", "特斯拉", "车联网",
        "车规级", "智能座舱", "线控底盘")),
    "energy": ("能源 / 新能源", (
        "光伏", "风电", "核电", "氢能", "储能", "电池", "锂",
        "特高压", "电网", "硅料", "HJT", "TOPCon", "虚拟电厂",
        "钙钛矿", "超导", "水电", "燃气", "页岩气", "BC电池",
        "固态电池")),
    "bio": ("生物医药 / 健康", (
        "医药", "医疗", "生物", "疫苗", "中药", "创新药", "CXO",
        "医疗器械", "基因", "医院", "兽药", "医美", "肝炎", "抗癌",
        "流感", "生物医药", "GLP")),
    "space": ("航天 / 太空", (
        "航天", "卫星", "军工", "无人机", "太空", "北斗", "低空",
        "eVTOL", "大飞机", "通航", "导弹", "航空", "船舶", "军民融合",
        "海工装备")),
    "security": ("网络安全", (
        "网络安全", "数据安全", "信息安全", "密码")),
    "consumer": ("消费电子 / 数码", (
        "消费电子", "苹果", "手机", "光学镜头", "光学元件", "光学模组",
        "耳机", "可穿戴", "折叠屏", "智能音箱", "电子纸", "摄像头",
        "MiniLED", "OLED", "VR", "AR眼镜", "智能手表", "TWS")),
    "tech": ("科技 / 互联网", (
        "软件", "游戏", "传媒", "电商", "广告", "云计算",
        "SaaS", "直播", "短视频", "信创", "数据要素", "小程序",
        "抖音", "快手", "影视", "出版", "互联网金融", "互联网电商")),
    "macro": ("财经 / 宏观", (
        "银行", "证券", "保险", "地产", "房地产", "水泥", "钢铁",
        "煤炭", "有色", "黄金", "白酒", "食品", "家电", "基建",
        "建筑", "港口", "航运", "旅游", "零售", "化工", "农业",
        "猪肉", "乳业")),
    "science": ("科学 / 前沿", (
        "量子", "脑机接口", "6G", "可控核聚变", "合成生物", "室温超导",
        "商业航天", "太空旅游")),
}

_INDUSTRY_WEIGHT = 2
_CONCEPT_WEIGHT = 1
_MAX_TRACKS = 3


def _hit(text: str, keywords: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in keywords)


def match_tracks(industry: str | None, concepts: list[str],
                 limit: int = _MAX_TRACKS) -> list[str]:
    """行业名 + 概念名 → 相关赛道 key 列表（按相关度排序，最多 limit 个）。"""
    scores: dict[str, int] = {}
    if industry:
        for key, (_name, kws) in TRACKS.items():
            if _hit(industry, kws):
                scores[key] = scores.get(key, 0) + _INDUSTRY_WEIGHT
    for concept in concepts or []:
        for key, (_name, kws) in TRACKS.items():
            if _hit(concept, kws):
                scores[key] = scores.get(key, 0) + _CONCEPT_WEIGHT
    ordered = sorted(
        scores, key=lambda k: (-scores[k], list(TRACKS).index(k)))
    return [k for k in ordered if scores[k] > 0][:limit]


def track_name(key: str) -> str | None:
    entry = TRACKS.get(key)
    return entry[0] if entry else None
