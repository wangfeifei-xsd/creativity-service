"""跨语言委托签名使用同一组公开向量。"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from creativity_service.integrations.business.delegation import (
    DelegationClaims,
    sign,
    split_envelope,
    verify_payload,
)


def test_javascript_and_python_signature_vectors():
    vectors = json.loads(Path("contracts/integrations/delegation-vectors.json").read_text())
    node = shutil.which("node") or str(Path("../.tools/js/node_modules/.bin/node").resolve())
    for vector in vectors:
        claims = DelegationClaims.model_validate(vector["claims"])
        expected = sign(claims, vector["kid"], bytes.fromhex(vector["secret_hex"]))
        assert expected == vector["envelope"]
        result = subprocess.run(
            [node, "contracts/integrations/sign-vector.mjs"],
            input=json.dumps(vector),
            text=True,
            capture_output=True,
            check=True,
        )
        assert result.stdout.strip() == expected
        kid, payload, signature = split_envelope(expected)
        assert (
            verify_payload(kid, payload, signature, bytes.fromhex(vector["secret_hex"])) == claims
        )
        assert claims.request.body_sha256 == hashlib.sha256(vector["body"].encode()).hexdigest()
