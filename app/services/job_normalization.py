"""Small explicit synonym map for fixture search; no semantic ranking claims."""
from functools import lru_cache
import re
import unicodedata

from app.services.skill_taxonomy import ALIASES as SKILL_ALIASES, canonical_skill, normalize_phrases



def normalize_skill(value: str) -> str:
    return canonical_skill(value)


def normalized_skills(values: list[str]) -> list[str]:
    return sorted({normalize_skill(value) for value in values if value.strip()})


@lru_cache(maxsize=32768)
def search_text(value: str) -> str:
    value = normalize_phrases(value)
    for before, after in [('back-end', 'backend'), ('front-end', 'frontend'), ('full-stack', 'full stack'), ('fullstack', 'full stack')]:
        value = value.replace(before, after)
    words = re.findall(r'[\w+#.]+(?:/[\w+#.]+)?', value)
    return ' '.join(SKILL_ALIASES.get(word, word) for word in words)
