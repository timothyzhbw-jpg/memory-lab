"""Jev verifier: a dedicated judgment model (natural language inference cross-encoder).

Unlike the LLM verifier it does not generate text: it scores the passage (premise) against the
claim (hypothesis) as entailment / neutral / contradiction. It runs locally, gives the same answer
every time and reports a probability, which makes it a useful independent second opinion.

Install with ``pip install 'memfalsify[jev]'``. Any Hugging Face sequence-classification model
whose labels are entailment / neutral / contradiction works; the default is multilingual
(including Chinese).
"""
from __future__ import annotations

from collections.abc import Callable

from .verify import Judgment, VerifierError

DEFAULT_MODEL = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
RELATIONS = {"entailment": "supports", "contradiction": "contradicts", "neutral": "unrelated"}

# (premise, hypothesis) -> {label: probability}
Classifier = Callable[[str, str], dict[str, float]]


class JevVerifier:
    """Map NLI labels to relations; a top label below ``threshold`` counts as unrelated."""

    def __init__(self, model: str = DEFAULT_MODEL, threshold: float = 0.5,
                 classify: Classifier | None = None, device: str | None = None):
        self.classify = classify or load_classifier(model, device)
        self.threshold = threshold
        self.name = f"jev:{model}"

    def judge(self, claim: str, passage: str) -> Judgment:
        scores = {label.lower(): p for label, p in self.classify(passage, claim).items()}
        if missing := set(RELATIONS) - scores.keys():
            raise VerifierError(f"{self.name}: model labels {sorted(scores)} lack {sorted(missing)}")
        label = max(RELATIONS, key=scores.__getitem__)
        rationale = ", ".join(f"{name} {scores[name]:.2f}" for name in RELATIONS)
        relation = RELATIONS[label]
        if scores[label] < self.threshold:
            relation = "unrelated"
            rationale += f" (top label below threshold {self.threshold})"
        return Judgment(relation=relation, rationale=rationale, verifier=self.name, confidence=scores[label])


def load_classifier(model: str, device: str | None = None) -> Classifier:
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:
        raise VerifierError("the Jev verifier needs torch and transformers: pip install 'memfalsify[jev]'") from exc
    tokenizer = AutoTokenizer.from_pretrained(model)
    network = AutoModelForSequenceClassification.from_pretrained(model).eval()
    if device:
        network.to(device)
    labels = network.config.id2label

    def classify(premise: str, hypothesis: str) -> dict[str, float]:
        inputs = tokenizer(premise, hypothesis, truncation=True, return_tensors="pt").to(network.device)
        with torch.no_grad():
            probabilities = network(**inputs).logits.softmax(-1)[0].tolist()
        return {labels[i]: p for i, p in enumerate(probabilities)}

    return classify
