import argparse
import json
from pathlib import Path
from .evidence import load_evidence, ValidationError
from .audit import audit
from .state import replay

def main():
    parser = argparse.ArgumentParser(description='Offline evidence audit and organizational event replay')
    parser.add_argument('command', choices=['audit', 'replay', 'demo'])
    parser.add_argument('dataset', type=Path, help='JSON dataset; evidence paths are relative to this file')
    parser.add_argument('--as-of', help='Replay effective-time cutoff, with timezone')
    args = parser.parse_args()
    try:
        data = json.loads(args.dataset.read_text(encoding='utf-8'))
        evidence = load_evidence(data['evidence'], args.dataset.parent)
        result = {}
        if args.command in ('audit', 'demo'):
            result['memfalsify'] = audit(data['memories'], data['assertions'], evidence)
        if args.command in ('replay', 'demo'):
            result['organizational_memory'] = replay(data['events'], data['policy'], evidence, args.as_of)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f'Invalid dataset: {exc}\n')
