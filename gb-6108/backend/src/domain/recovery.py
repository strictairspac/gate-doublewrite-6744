from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    label: str
    note: str
    image: object | None


def choose_copy(key, durable_generation, target, shadow):
    target_good = target.valid(key) and target.generation == durable_generation
    shadow_good = (
        shadow is not None and shadow.valid(key) and shadow.generation == durable_generation
    )
    if target_good and shadow_good and target.checksum != shadow.checksum:
        return Decision("隔离", "同一代两份有效内容冲突", None)
    if target_good:
        return Decision("保留数据页", "数据页已达到已知持久代", target)
    if shadow_good:
        return Decision("从双写区修复", "双写页校验通过，且代次达到持久线", shadow)
    return Decision("隔离", "已知持久代无可用副本", None)
