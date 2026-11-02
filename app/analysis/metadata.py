"""Reproduce the portable joint metadata from the pinned robot common file."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from app.schemas.rest import JointDescriptor, JointMetadata
from replay_protocol import CANONICAL_JOINTS
from replay_protocol.limits import LimitSpec

SOURCE_REF = 'assets/robots/r1pro_2026/isaacsim_6_x/reproduction/configs/r1pro_2026_common.json'
CACHE = Path(__file__).resolve().parents[1] / 'assets/robots/r1pro_2026_joints.json'


@dataclass(frozen=True)
class MetadataArtifact:
    metadata: JointMetadata
    source_sha256: str

    def specs(self):
        return tuple(LimitSpec(joint.type, joint.lower, joint.upper) for joint in self.metadata.joints)

    def payload(self):
        return {**self.metadata.model_dump(mode='json'), 'source_sha256': self.source_sha256}


def metadata_from_common(path, *, robot_id='galaxea_r1pro_2026', source_ref=SOURCE_REF):
    raw = Path(path).read_bytes()
    try:
        source = json.loads(raw)
        joints = []
        if set(source['joints']) != set(CANONICAL_JOINTS):
            raise ValueError('Common config does not contain exactly the canonical joints')
        for name in CANONICAL_JOINTS:
            entry = source['joints'][name]
            kind = entry['type']
            joints.append(JointDescriptor(name=name, type=kind, unit='m' if kind == 'prismatic' else 'rad',
                lower=None if kind == 'continuous' else entry['limit']['lower'],
                upper=None if kind == 'continuous' else entry['limit']['upper'],
                limits_provenance=entry['limits_provenance']))
        metadata = JointMetadata(robot_id=robot_id, canonical_order=list(CANONICAL_JOINTS),
                                 joints=joints, limits_source=source_ref)
        return MetadataArtifact(metadata, hashlib.sha256(raw).hexdigest())
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'Invalid robot common metadata: {exc}') from exc


def load_joint_metadata(path=CACHE):
    raw = json.loads(Path(path).read_text())
    digest = raw.pop('source_sha256', None)
    if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
        raise ValueError('Joint metadata must retain its common-file SHA256')
    return MetadataArtifact(JointMetadata.model_validate(raw), digest)
