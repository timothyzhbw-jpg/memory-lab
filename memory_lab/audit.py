"""MemFalsify: challenge claims using explicit, cited assertions."""
from .evidence import cited, ValidationError

def audit(memories, assertions, evidence):
    ids = [m['id'] for m in memories]
    if len(set(ids)) != len(ids):
        raise ValidationError('duplicate memory id')
    for a in assertions:
        if a['memory_id'] not in ids or a['relation'] not in ('supports', 'contradicts'):
            raise ValidationError('invalid assertion')
        cited(evidence, a['evidence_ids'])
    output = []
    for m in memories:
        matches = [a for a in assertions if a['memory_id'] == m['id']]
        support = [a for a in matches if a['relation'] == 'supports']
        contrary = [a for a in matches if a['relation'] == 'contradicts']
        status = ('conflicted' if support else 'challenged') if contrary else ('supported' if support else 'insufficient_evidence')
        refs = sorted({i for a in matches for i in a['evidence_ids']})
        output.append({'memory_id': m['id'], 'original': m['text'], 'status': status,
                       'evidence': cited(evidence, refs) if refs else [],
                       'challenge': 'Resolve conflicting assertions with an authorized reviewer.' if status == 'conflicted' else
                                    'Review contrary evidence before retaining this memory.' if contrary else
                                    'Provide traceable evidence.' if not support else None,
                       'revision_proposal': {'operation': 'flag_for_review', 'reason': status,
                                             'replacement': None} if status != 'supported' else None})
    return {'mode': 'read_only', 'findings': output,
            'limitations': 'Assertions are supplied interpretations. Hashes prove integrity, not source truth or authority.'}
