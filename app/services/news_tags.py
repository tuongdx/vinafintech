from collections.abc import Iterable


DEFAULT_NEWS_TAGS = ("VinaFintech", "congnghephantichdaututaisanso")


def with_default_news_tags(tags: str | Iterable[str] | None) -> str:
    if isinstance(tags, str):
        values = tags.split(",")
    elif tags is None:
        values = []
    else:
        values = tags

    normalized: list[str] = []
    seen: set[str] = set()
    for value in (*values, *DEFAULT_NEWS_TAGS):
        tag = str(value).strip().lstrip("#").strip()
        key = tag.casefold()
        if tag and key not in seen:
            normalized.append(tag)
            seen.add(key)

    return ", ".join(normalized)
