"""Test-only issuers and policy-selected observers; no test key enters runtime code."""
import base64
import json
import sys
from pathlib import Path
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from approval_auth import signing_bytes

SIGNER = Ed25519PrivateKey.generate()


def policy(repo, update):
    path = repo / '.orchestrator/config.yaml'
    doc = yaml.safe_load(path.read_text()) if path.exists() else {}
    doc = doc or {}
    evidence = doc.setdefault('orchestrator', {}).setdefault('evidence', {})
    update(evidence)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=True))


def publish_key(repo, approver):
    def configure(evidence):
        keys = evidence.setdefault('approval_keys', {})
        key = keys.setdefault('test-issuer', {
            'public_key':base64.b64encode(SIGNER.public_key().public_bytes_raw()).decode(),
            'approvers':[], 'channels':['host_approval_event']})
        if approver not in key['approvers']:
            key['approvers'].append(approver)
    policy(repo, configure)


def sign_event(repo, event):
    publish_key(repo, event["approver"])
    event = {**event, 'audience':str(repo.resolve()), 'key_id':'test-issuer'}
    event['signature'] = base64.b64encode(SIGNER.sign(signing_bytes(event))).decode()
    return event


def observer(repo, fact_path, value, inputs):
    """Exercise an approved parser reading fixture input, bound to actual file digests."""
    import evidence_provenance as ep
    directory = repo / '.orchestrator/evidence/observer-fixture'
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / 'observe.py'
    script.write_text('import json,sys\nfrom pathlib import Path\n'
        'for ref in sys.argv[3:]: Path(ref).read_bytes()\n'
        'print(json.dumps({"fact_path":sys.argv[1],"value":json.loads(Path(sys.argv[2]).read_text())}))\n')
    data = directory / (fact_path + '.json')
    data.write_text(json.dumps(value))
    args = [sys.executable, str(script), fact_path, str(data), *[str(repo / ref) for ref in inputs]]
    script_ref, data_ref = script.relative_to(repo).as_posix(), data.relative_to(repo).as_posix()
    rule = {'argv':args, 'files':{script_ref:ep.path_revision(repo, script_ref)},
            'inputs':[*inputs, data_ref]}
    policy(repo, lambda evidence: evidence.setdefault('observers', {}).update({fact_path:rule}))
    return args
