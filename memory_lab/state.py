"""Replay an immutable event log under a caller-supplied authority policy."""
from datetime import datetime
from .evidence import cited, ValidationError

ACTIONS = {'request', 'dependency', 'approve', 'reject', 'reconsider', 'revoke'}

def timestamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValidationError('timestamp must include timezone')
    return dt

def replay(events, policy, evidence, as_of=None):
    # Input order is the ingestion order. Equal effective times use that order.
    seen = set()
    parsed = []
    for index, event in enumerate(events):
        if event['id'] in seen:
            raise ValidationError('duplicate event id')
        seen.add(event['id'])
        if event['action'] not in ACTIONS:
            raise ValidationError('unknown action')
        cited(evidence, event['evidence_ids'])
        parsed.append((timestamp(event['timestamp']), index, event))
    cutoff = timestamp(as_of) if as_of else None
    states, trail = {}, []
    for when, _, e in sorted(parsed):
        if cutoff and when > cutoff:
            continue
        s = states.setdefault(e['feature'], {'status': 'unresolved', 'reconsideration_requested': False,
                            'dependencies': [], 'last_authorized_decision': None, 'pending': []})
        action = e['action']
        actor = e['actor']
        roles = policy['actors'].get(actor, [])
        allowed = bool(set(roles) & set(policy['permissions'].get(action, [])))
        before = s['status']
        reason = 'applied' if allowed else 'unauthorized'
        if allowed:
            if action in ('approve', 'reject'):
                s['status'] = 'approved' if action == 'approve' else 'rejected'
                s['last_authorized_decision'] = {'event_id': e['id'], 'actor': actor,
                    'action': action, 'timestamp': e['timestamp'], 'evidence_ids': e['evidence_ids']}
                s['reconsideration_requested'] = False
                s['pending'] = []
            elif action == 'revoke':
                decision = s['last_authorized_decision']
                if not decision or e.get('supersedes') != decision['event_id']:
                    reason = 'invalid_supersession'
                else:
                    s['status'] = 'unresolved'
                    s['last_authorized_decision'] = {'event_id': e['id'], 'actor': actor,
                        'action': action, 'timestamp': e['timestamp'], 'evidence_ids': e['evidence_ids']}
                    s['pending'] = ['product_manager_review']
            elif action == 'dependency':
                dep = e.get('dependency')
                if not isinstance(dep, str) or not dep:
                    raise ValidationError('dependency action requires a dependency')
                s['dependencies'] = sorted(set(s['dependencies'] + [dep]))
            elif action in ('request', 'reconsider'):
                s['pending'] = ['product_manager_review']
                if s['last_authorized_decision'] or action == 'reconsider':
                    s['reconsideration_requested'] = True
            if reason == 'applied':
                s['latest_event'] = e['id']
        trail.append({'event_id': e['id'], 'actor': actor, 'action': action,
                      'outcome': reason, 'before': before, 'after': s['status'],
                      'evidence_ids': e['evidence_ids']})
    return {'mode': 'read_only', 'states': states, 'audit_log': trail}
