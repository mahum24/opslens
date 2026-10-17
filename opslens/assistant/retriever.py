"""Find the policy and playbook passages that answer a question (the RAG part).

Documents are split at their "##" headings so each passage is one topic,
and searched with TF-IDF. For a few dozen passages this is fast, needs no
external service, and is easy to explain; swapping in embeddings later only
changes this file.
"""
import re
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from opslens.settings import DOCS_DIR

MIN_SCORE = 0.06


@dataclass
class Passage:
    doc: str
    section: str
    text: str
    score: float = 0.0

    @property
    def citation(self):
        return f"{self.doc} > {self.section}"


def load_passages(docs_dir=DOCS_DIR) -> list[Passage]:
    passages = []
    for path in sorted(docs_dir.glob("*.md")):
        text = path.read_text()
        title = re.search(r"^# (.+)$", text, re.M).group(1)
        parts = re.split(r"^## ", text, flags=re.M)
        intro = parts[0].split("\n", 1)[1].strip() if "\n" in parts[0] else ""
        if intro:
            passages.append(Passage(title, "Overview", intro))
        for part in parts[1:]:
            heading, _, body = part.partition("\n")
            passages.append(Passage(title, heading.strip(), body.strip()))
    return passages


class Retriever:
    def __init__(self, passages=None):
        self.passages = passages or load_passages()
        self.vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english")
        self.matrix = self.vec.fit_transform([f"{p.doc} {p.section} {p.text}" for p in self.passages])

    def search(self, query: str, k: int = 3) -> list[Passage]:
        scores = cosine_similarity(self.vec.transform([query]), self.matrix)[0]
        best = scores.argsort()[::-1][:k]
        return [Passage(**{**self.passages[i].__dict__, "score": round(float(scores[i]), 3)})
                for i in best if scores[i] >= MIN_SCORE]
