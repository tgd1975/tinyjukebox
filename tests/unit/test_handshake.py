"""The proofs of the handshake (docs/developer/library.md, section 4.14) against the test vectors that the sync module
of the app reads as well (app/sync/src/test/resources/handshake.json): both sides compute the same proofs."""

import json
from pathlib import Path

from tinyjukebox.sync import handshake

VECTORS = Path(__file__).parents[2] / "app" / "sync" / "src" / "test" / "resources" / "handshake.json"


def test_the_proofs_of_the_test_vectors():
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert handshake.fingerprint(vectors["certificate"]) == vectors["fingerprint"]
    assert len(vectors["proofs"]) == 6
    for case in vectors["proofs"]:
        proof = handshake.proof(
            case["password"],
            case["role"],
            case["box"],
            case["nonce_box"],
            case["nonce_library"],
            vectors["certificate"],
        )
        assert proof == case["proof"], case
