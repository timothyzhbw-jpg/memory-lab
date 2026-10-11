"""Optional extraction boundaries. No network calls or API keys are required."""
from typing import Protocol
from .evidence import cited, ValidationError

class CandidateExtractor(Protocol):
    def extract(self, evidence: dict) -> dict:
        """Return candidates plus requires_human_review; each candidate cites evidence IDs."""
        ...

class CallableExtractor:
    """Inject a local model or a caller-owned LLM/Jev callback; no SDK assumptions."""
    def __init__(self, callback):
        self.callback = callback

    def extract(self, evidence):
        candidates = self.callback(evidence)
        for candidate in candidates:
            cited(evidence, candidate['evidence_ids'])
            if candidate['relation'] not in ('supports', 'contradicts'):
                raise ValidationError('invalid adapter candidate')
        return {'candidates': candidates, 'requires_human_review': True}

class MultimodalSource(Protocol):
    def transcribe(self, asset: bytes) -> dict:
        """Return transcript, original_sha256, locator (page/time/region), tool/version.

        Persist original bytes and derived text separately. A reviewer must verify
        extracted spans before registering text as evidence. OCR and transcription
        are interpretations, never automatically authoritative decision events.
        """
        ...
