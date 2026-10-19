"""Small explicit synonym map for fixture search; no semantic ranking claims."""
from functools import lru_cache
import re
import unicodedata

SKILL_ALIASES = {
    'js': 'javascript', 'javascript': 'javascript', 'ts': 'typescript',
    'py': 'python', 'ml': 'machine learning', 'nlp': 'natural language processing',
    'postgres': 'postgresql', 'k8s': 'kubernetes', 'react.js': 'react',
    'reactjs': 'react', 'nodejs': 'node.js', 'testing': 'software testing',
    'c/c++': 'c++', 'ci / cd': 'ci/cd', 'powerbi': 'power bi',
}


def normalize_skill(value: str) -> str:
    value = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value).strip()).casefold()
    return SKILL_ALIASES.get(value, value)


def normalized_skills(values: list[str]) -> list[str]:
    return sorted({normalize_skill(value) for value in values if value.strip()})


@lru_cache(maxsize=4096)
def search_text(value: str) -> str:
    value = unicodedata.normalize('NFKC', value).casefold()
    for before, after in [('back-end', 'backend'), ('front-end', 'frontend'), ('full-stack', 'full stack'), ('fullstack', 'full stack')]:
        value = value.replace(before, after)
    words = re.findall(r'[\w+#.]+(?:/[\w+#.]+)?', value)
    return ' '.join(SKILL_ALIASES.get(word, word) for word in words)
