"""The versioned evaluation set: knowledge corpus + questions with expected sources and references.

Files live in `evaluacion/rag/` (corpus.json, preguntas.json). Each question names the document(s)
that answer it (`fuentes`), a reference answer and the key facts a correct answer must contain;
out-of-domain questions have no sources (the right behaviour is to abstain).
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "evaluacion" / "rag"

KINDS = ("literal", "parafrasis", "multi", "fuera_de_dominio")


def fold(text: str) -> str:
    """Lowercase, without diacritics and with single spaces (how key facts are matched)."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    content: str
    tags: tuple[str, ...]
    origin: str

    @property
    def fragment(self) -> str:
        """The text an agent sees for this document (title + content, as in the knowledge context)."""
        return f"{self.title}\n{self.content}"


@dataclass(frozen=True)
class Question:
    id: str
    kind: str
    text: str
    sources: tuple[str, ...]
    reference: str
    key_facts: tuple[str, ...]

    @property
    def answerable(self) -> bool:
        return bool(self.sources)


@dataclass(frozen=True)
class EvalSet:
    version: str
    documents: tuple[Document, ...]
    questions: tuple[Question, ...]

    def document(self, document_id: str) -> Document:
        return self._by_id()[document_id]

    def _by_id(self) -> dict[str, Document]:
        return {d.id: d for d in self.documents}


def load_eval_set(data_dir: Path = DATA_DIR) -> EvalSet:
    corpus = json.loads((data_dir / "corpus.json").read_text(encoding="utf-8"))
    questions = json.loads((data_dir / "preguntas.json").read_text(encoding="utf-8"))
    return EvalSet(
        version=f"{corpus['version']}/{questions['version']}",
        documents=tuple(
            Document(id=d["id"], title=d["titulo"], content=d["contenido"],
                     tags=tuple(d.get("etiquetas", ())), origin=d.get("origen", "sintetico"))
            for d in corpus["documentos"]
        ),
        questions=tuple(
            Question(id=q["id"], kind=q["tipo"], text=q["pregunta"], sources=tuple(q["fuentes"]),
                     reference=q["respuesta_referencia"], key_facts=tuple(q["hechos_clave"]))
            for q in questions["preguntas"]
        ),
    )


def validate(eval_set: EvalSet) -> list[str]:
    """Structural errors of the set (empty list = consistent). Run by the tests and by the gate."""
    errors: list[str] = []
    doc_ids = [d.id for d in eval_set.documents]
    if len(doc_ids) != len(set(doc_ids)):
        errors.append("ids de documento repetidos")
    question_ids = [q.id for q in eval_set.questions]
    if len(question_ids) != len(set(question_ids)):
        errors.append("ids de pregunta repetidos")
    known = set(doc_ids)
    cited: set[str] = set()
    for q in eval_set.questions:
        if q.kind not in KINDS:
            errors.append(f"{q.id}: tipo desconocido {q.kind!r}")
        if q.kind == "fuera_de_dominio" and (q.sources or q.key_facts):
            errors.append(f"{q.id}: fuera de dominio con fuentes o hechos")
        if q.kind != "fuera_de_dominio" and not (q.sources and q.key_facts):
            errors.append(f"{q.id}: sin fuentes o sin hechos clave")
        if q.kind == "multi" and len(q.sources) < 2:
            errors.append(f"{q.id}: «multi» con menos de dos fuentes")
        for source in q.sources:
            if source not in known:
                errors.append(f"{q.id}: la fuente {source} no existe")
        cited.update(q.sources)
        texts = [fold(eval_set.document(s).content) for s in q.sources if s in known]
        for fact in q.key_facts:
            if not any(fold(fact) in t for t in texts):
                errors.append(f"{q.id}: el hecho clave {fact!r} no aparece en sus fuentes")
    for missing in sorted(known - cited):
        errors.append(f"{missing}: ningún caso lo usa como fuente")
    return errors
