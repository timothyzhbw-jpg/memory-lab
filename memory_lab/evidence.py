"""Evidence integrity and explicit structured assertions; never infer truth from recency."""
import hashlib
from datetime import datetime
from pathlib import Path

class ValidationError(ValueError):
    pass

def digest(data):
    return hashlib.sha256(data).hexdigest()

def load_evidence(records, base):
    result = {}
    base = Path(base).resolve()
    for item in records:
        if item['id'] in result:
            raise ValidationError('duplicate evidence id')
        for key in ('source_uri', 'source_kind', 'captured_at'):
            if not isinstance(item.get(key), str) or not item[key]:
                raise ValidationError('missing evidence provenance: ' + key)
        captured = datetime.fromisoformat(item['captured_at'].replace('Z', '+00:00'))
        if captured.tzinfo is None:
            raise ValidationError('capture timestamp must include timezone')
        path = (base / item['path']).resolve()
        if not path.is_relative_to(base):
            raise ValidationError('evidence path escapes dataset directory')
        raw = path.read_bytes()
        if digest(raw) != item['sha256']:
            raise ValidationError('evidence hash mismatch: ' + item['id'])
        text = raw.decode('utf-8')
        quote = item['quote']
        start = item['start']
        end = item['end']
        if not quote or start < 0 or end <= start or text[start:end] != quote:
            raise ValidationError('invalid exact evidence span: ' + item['id'])
        result[item['id']] = dict(item)
    return result

def cited(evidence, ids):
    if not ids or any(i not in evidence for i in ids):
        raise ValidationError('missing or unknown evidence reference')
    return [evidence[i] for i in ids]
