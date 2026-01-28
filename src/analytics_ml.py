import math
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from sqlmodel import Session, select

from .models import Incident, Event

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "is", "it", "of", "on", "or", "that", "the", "to", "was", "were", "with",
    "down", "error", "failed", "failure", "timeout", "http", "status",
}


def tokenize(text: Optional[str]) -> List[str]:
    if not text:
        return []
    low = text.lower()
    low = re.sub(r"https?://\S+", " url ", low)
    low = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", " ip ", low)
    low = re.sub(r"\d+", " # ", low)
    low = re.sub(r"[^a-z0-9_\s]", " ", low)
    low = re.sub(r"\s+", " ", low).strip()
    tokens = [t for t in low.split() if t not in _STOPWORDS and len(t) > 2]
    return tokens


def _build_tfidf(docs: List[List[str]]) -> List[Dict[str, float]]:
    if not docs:
        return []
    df: Dict[str, int] = {}
    for tokens in docs:
        for tok in set(tokens):
            df[tok] = df.get(tok, 0) + 1
    n = len(docs)
    vectors: List[Dict[str, float]] = []
    for tokens in docs:
        tf: Dict[str, int] = {}
        for tok in tokens:
            tf[tok] = tf.get(tok, 0) + 1
        vec: Dict[str, float] = {}
        for tok, count in tf.items():
            idf = math.log((n + 1) / (df.get(tok, 0) + 1)) + 1.0
            vec[tok] = float(count) * idf
        vectors.append(vec)
    return vectors


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    for tok, val in a.items():
        if tok in b:
            dot += val * b[tok]
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def find_similar_incidents(
    session: Session,
    project_id: int,
    target_text: Optional[str],
    days: int = 90,
    limit: int = 5,
    threshold: float = 0.35,
    target_incident_id: Optional[int] = None,
) -> List[dict]:
    target_tokens = tokenize(target_text)
    if not target_tokens:
        return []

    start_dt = datetime.utcnow() - timedelta(days=days)
    incs = session.exec(
        select(Incident).where(
            Incident.project_id == project_id,
            Incident.started_at >= start_dt,
        )
    ).all()
    docs: List[List[str]] = []
    meta: List[dict] = []
    for inc in incs:
        if target_incident_id and inc.id == target_incident_id:
            continue
        ev = session.exec(select(Event).where(Event.incident_id == inc.id).order_by(Event.created_at)).first()
        msg = getattr(ev, "message", None) if ev else None
        tokens = tokenize(msg)
        if not tokens:
            continue
        docs.append(tokens)
        meta.append({
            "incident_id": inc.id,
            "check_id": inc.check_id,
            "started_at": inc.started_at.isoformat(),
        })

    if not docs:
        return []
    docs_all = docs + [target_tokens]
    vectors = _build_tfidf(docs_all)
    target_vec = vectors[-1]
    matches = []
    for i, m in enumerate(meta):
        score = _cosine(vectors[i], target_vec)
        if score >= threshold:
            matches.append({**m, "score": round(score, 4)})
    matches.sort(key=lambda r: r["score"], reverse=True)
    return matches[:limit]
