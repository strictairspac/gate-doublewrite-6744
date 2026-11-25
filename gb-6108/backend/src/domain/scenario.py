import re

from .fixtures import FIXTURES
from .image import Image
from .recovery import choose_copy


CONFIG = {
    "title": "闸北水工·启闭日志双写巡检",
    "team": "闸北水工 9 人闸控值守与数据库巡检组",
    "description": "在值守交班前识别撕裂、旧代或双损日志页，生成可修复与隔离清单。",
    "direction": "双写缓冲与数据库页撕裂恢复",
    "mechanism": "字节级部分写入与页号、代次、内容校验，按已知持久线选择可恢复页。",
    "modules": ["启闭值守", "故障页清单", "双写复核", "修复隔离单"],
    "unit": "掉电巡检单",
    "max_parameter": 128,
    "parameter_label": "撕裂写入前缀（字节，1–128）",
    "default_parameter": 6,
    "facts": ["已修复", "隔离页", "原页通过"],
    "palette": 8,
    "fixture": "GT-N1-1124 / 北一闸",
}
EVENTS = {
    "complete": "写入完成",
    "torn": "目标页撕裂",
    "both": "双份损坏",
    "before_shadow": "双写前断电",
    "before_target": "目标写入前断电",
    "stale": "双写区只剩旧代",
    "disagree": "同代副本冲突",
}


def normalize(payload):
    if not isinstance(payload, dict):
        raise ValueError("输入需为对象")
    pages = payload.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 80:
        raise ValueError("pages 需有 1 至 80 个日志页")
    seen = set()
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("日志页需为对象")
        key = page.get("page")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", key) or key in seen:
            raise ValueError("日志页号须唯一且仅含 1 至 32 位 ASCII 字母、数字、连字符或下划线")
        seen.add(key)
        if type(page.get("generation")) is not int or not 1 <= page["generation"] <= 1_000_000:
            raise ValueError("generation 需为 1 至 1000000 的整数")
        event = page.get("event")
        if not isinstance(event, str) or event not in EVENTS:
            raise ValueError("不支持该断电阶段")
        for key in ("before", "after"):
            value = page.get(key)
            if not isinstance(value, str) or not 1 <= len(value) <= 120:
                raise ValueError("前后日志内容须各有 1 至 120 字符")
            try:
                value.encode()
            except UnicodeEncodeError:
                raise ValueError("前后日志需为有效 UTF-8") from None
    return pages


def validate(payload):
    normalize(payload)


def fault_images(page, prefix_bytes):
    key, generation, event = page["page"], page["generation"], page["event"]
    prior = Image.seal(key, generation - 1, page["before"])
    intended = Image.seal(key, generation, page["after"])
    # Tear must leave at least the final byte unwritten for a multi-byte page.
    written = min(prefix_bytes, len(intended.data) - 1)
    broken = intended.torn(prior, written)
    match event:
        case "complete":
            return generation, intended, intended, None
        case "torn":
            return generation, broken, intended, written
        case "both":
            return generation, broken.damaged(), intended.damaged(), written
        case "before_shadow":
            return generation - 1, prior, None, None
        case "before_target":
            return generation, prior, intended, None
        case "stale":
            return generation, broken.damaged(), prior, written
        case "disagree":
            divergent = Image.seal(key, generation, page["after"] + " / 对侧登记")
            return generation, intended, divergent, None


def run(payload, parameter, db):
    pages = normalize(payload)
    rows = []
    counts = {"已修复": 0, "隔离页": 0, "原页通过": 0}
    metrics_by_decision = {"从双写区修复": "已修复", "隔离": "隔离页", "保留数据页": "原页通过"}
    for page in pages:
        key = page["page"]
        durable, target, shadow, written = fault_images(page, parameter)
        decision = choose_copy(key, durable, target, shadow)
        counts[metrics_by_decision[decision.label]] += 1
        rows.append({
            "页号": key, "掉电阶段": EVENTS[page["event"]], "持久代": durable,
            "目标代": target.generation, "目标校验": "通过" if target.valid(key) else "损坏",
            "双写代": shadow.generation if shadow else None,
            "双写校验": ("通过" if shadow.valid(key) else "损坏") if shadow else "无副本",
            "前缀写入字节": written, "结论": decision.label, "说明": decision.note,
            "读取文本": decision.image.data.decode() if decision.image else None,
        })
    return {
        "summary": f"核查 {len(pages)} 页，修复 {counts['已修复']} 页，隔离 {counts['隔离页']} 页。",
        "metrics": counts,
        "rows": rows,
    }
