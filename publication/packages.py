import hashlib
import json
from dataclasses import dataclass

from intake.weighings import require_kilograms, require_label

from .evidence import JointEvidence, validate_evidence


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _seal_id(batch, revision):
    return 'seal-' + hashlib.sha256(_canonical_json([batch, revision])).hexdigest()


@dataclass(frozen=True)
class V1Package:
    seal_id: str
    batch: str
    revision: str
    evidence: JointEvidence

    @property
    def buckets(self):
        return self.evidence.buckets

    def document(self):
        return {
            'version': 1,
            'seal_id': self.seal_id,
            'batch': self.batch,
            'revision': self.revision,
            'buckets': self.buckets,
            'evidence': {
                'operation_count': self.evidence.operation_count,
                'sources': self.evidence.sources,
                'arcs': self.evidence.arcs,
                'terminals': self.evidence.terminals,
            },
        }


def make_v1_package(batch, revision, evidence):
    batch = require_label(batch, 'batch')
    revision = require_label(revision, 'batch revision')
    validate_evidence(evidence)
    canonical = JointEvidence(
        evidence.operation_count, tuple(sorted(evidence.sources)),
        tuple(sorted(evidence.arcs)), tuple(sorted(evidence.terminals)),
    )
    return V1Package(_seal_id(batch, revision), batch, revision, canonical)


def fingerprint_package(package):
    return hashlib.sha256(_canonical_json(package.document())).hexdigest()


def encode_v1_package(package):
    document = package.document()
    document['fingerprint'] = fingerprint_package(package)
    return _canonical_json(document)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate package field {key}')
        result[key] = value
    return result


def _records(value, width):
    if not isinstance(value, list):
        raise ValueError('package records must be an array')
    for record in value:
        if not isinstance(record, list) or len(record) != width:
            raise ValueError('invalid package record')
    return tuple(tuple(record) for record in value)


def decode_v1_package(data):
    if not isinstance(data, (bytes, str)):
        raise ValueError('V1 input must be JSON bytes or text')
    try:
        document = json.loads(data, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise ValueError('invalid package JSON') from exc
    fields = {'version', 'seal_id', 'batch', 'revision', 'buckets', 'evidence', 'fingerprint'}
    if not isinstance(document, dict) or set(document) != fields:
        raise ValueError('invalid V1 package fields')
    if type(document['version']) is not int or document['version'] != 1:
        raise ValueError('unsupported package version')
    payload = document['evidence']
    if not isinstance(payload, dict) or set(payload) != {'operation_count', 'sources', 'arcs', 'terminals'}:
        raise ValueError('invalid material evidence fields')
    evidence = JointEvidence(
        payload['operation_count'], _records(payload['sources'], 3),
        _records(payload['arcs'], 3), _records(payload['terminals'], 3),
    )
    package = make_v1_package(document['batch'], document['revision'], evidence)
    buckets = tuple(sorted(
        (require_label(name, 'bucket'), require_kilograms(weight))
        for name, weight in _records(document['buckets'], 2)
    ))
    if document['seal_id'] != package.seal_id or buckets != package.buckets:
        raise ValueError('package identity or terminal directory disagrees with evidence')
    if document['fingerprint'] != fingerprint_package(package):
        raise ValueError('package content fingerprint does not match')
    return package
