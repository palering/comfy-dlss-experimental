"""Human-readable UI choices; stable numeric IDs remain the wire contract."""

LOOK_CHOICES = {
    "nr_style": ("默认风格", "自然 · Natural", "电影感 · Cinematic"),
    "nr_preset": ("模型默认（建议）", "实验预设 A", "实验预设 B", "实验预设 C"),
    "worker_profile": ("实验配置 A", "当前兼容配置（建议）", "实验配置 B", "实验配置 C"),
}


def choice_id(name: str, value: str | int) -> int:
    """Accept exact current captions or legacy integer IDs, never coerce."""
    choices = LOOK_CHOICES[name]
    if type(value) is int and 0 <= value < len(choices):
        return value
    if type(value) is str and value in choices:
        return choices.index(value)
    raise ValueError(f"{name}: choose one of {', '.join(choices)} (legacy integer IDs also accepted)")


def legacy_labels(name: str) -> dict[str, str]:
    # Frontend migration reads this metadata rather than duplicating captions.
    return {str(index): label for index, label in enumerate(LOOK_CHOICES[name])}
