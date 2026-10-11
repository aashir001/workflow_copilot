"""RAG over SOPs: parse SOP markdown, chunk by section, retrieve with TF-IDF.

Retrieval blends a word (1-2 gram) TF-IDF cosine with a character n-gram cosine so that
typos and morphological variants ("licence"/"license", "offboard"/"offboarding") still match.
Everything runs locally with scikit-learn; no model download or API key is needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from sklearn.feature_extraction.text import TfidfVectorizer

FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)
WORD_WEIGHT = 0.5


@dataclass
class SOP:
    intent: str
    sop_id: str
    title: str
    risk: str
    target_scoped: bool
    allowed_roles: list[str]
    approver_roles: list[str]
    sensitive_tools: list[str]
    requires: list[str]
    actions: list[dict]
    body: str
    sections: dict[str, str]

    @property
    def allowed_tools(self) -> set[str]:
        return {a["tool"] for a in self.actions}


@dataclass
class Chunk:
    intent: str
    sop_id: str
    section: str
    text: str

    @property
    def ref(self) -> str:
        return f"{self.sop_id}#{self.section}"


def parse_sop(path: Path) -> SOP:
    raw = path.read_text(encoding="utf-8")
    m = FRONT_RE.match(raw)
    if not m:
        raise ValueError(f"{path.name}: missing YAML front matter")
    meta = yaml.safe_load(m.group(1))
    body = m.group(2)
    sections: dict[str, str] = {}
    current = None
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = ""
        elif current is not None:
            sections[current] += line + "\n"
    sections = {k: v.strip() for k, v in sections.items()}
    return SOP(
        intent=meta["intent"],
        sop_id=meta["sop_id"],
        title=meta["title"],
        risk=meta.get("risk", "low"),
        target_scoped=bool(meta.get("target_scoped", False)),
        allowed_roles=list(meta.get("allowed_roles", [])),
        approver_roles=list(meta.get("approver_roles", [])),
        sensitive_tools=list(meta.get("sensitive_tools", [])),
        requires=list(meta.get("requires", [])),
        actions=list(meta.get("actions", [])),
        body=body,
        sections=sections,
    )


def load_sops(sop_dir: Path) -> dict[str, SOP]:
    sops: dict[str, SOP] = {}
    for p in sorted(Path(sop_dir).glob("*.md")):
        sop = parse_sop(p)
        sops[sop.intent] = sop
    if not sops:
        raise FileNotFoundError(f"no SOPs found in {sop_dir}")
    return sops


class Retriever:
    def __init__(self, sops: dict[str, SOP]):
        self.sops = sops
        self.chunks: list[Chunk] = []
        for sop in sops.values():
            for section, text in sop.sections.items():
                self.chunks.append(Chunk(sop.intent, sop.sop_id, section, f"{sop.title}. {section}. {text}"))
        docs = [c.text for c in self.chunks]
        self._word = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english")
        self._char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        self._xw = self._word.fit_transform(docs)
        self._xc = self._char.fit_transform(docs)

    def _scores(self, query: str):
        qw = self._word.transform([query])
        qc = self._char.transform([query])
        sw = (self._xw @ qw.T).toarray().ravel()
        sc = (self._xc @ qc.T).toarray().ravel()
        return WORD_WEIGHT * sw + (1 - WORD_WEIGHT) * sc

    def search(self, query: str, k: int = 4) -> list[tuple[Chunk, float]]:
        scores = self._scores(query)
        order = scores.argsort()[::-1][:k]
        return [(self.chunks[i], float(scores[i])) for i in order]

    def intent_scores(self, query: str) -> dict[str, float]:
        """Best chunk score per intent (one SOP = one candidate intent)."""
        scores = self._scores(query)
        best: dict[str, float] = {}
        for chunk, s in zip(self.chunks, scores):
            if s > best.get(chunk.intent, -1.0):
                best[chunk.intent] = float(s)
        return dict(sorted(best.items(), key=lambda kv: -kv[1]))

    def procedure_steps(self, intent: str, max_chars: int = 420) -> str:
        """Condensed 'Procedure' section of an SOP, used as grounded advice in replies."""
        text = self.sops[intent].sections.get("Procedure", "")
        lines = [re.sub(r"^\d+\.\s*", "", ln).strip() for ln in text.splitlines() if ln.strip()]
        out = " ".join(f"({i}) {ln}" for i, ln in enumerate(lines, 1))
        return out[:max_chars].rstrip()
