# Sample receipts: the sample bakery

Signed receipts from one run of the kit's demo on the made-up Sample Bakery: two applied reorders and the receipts of
the run that staged them and the pass that applied them. The business, its suppliers and its orders are samples, not
a real business. The key pair was made for this export; its private key was deleted after signing, so no one can
add to these receipts.

- `receipts.json`: the four receipts, as `sovereign-agents receipts export` writes them.
- `ed25519.pem`: the sample business's public key.

## Check them yourself

Change one character inside any `canonical` and every check below fails.

In a GitHub workflow, with [verify-receipts-action](https://github.com/christiaan839-beep/verify-receipts-action)
(this repository runs it on every push, in `.github/workflows/sample-receipts.yml`):

```yaml
- uses: christiaan839-beep/verify-receipts-action@e0061c5da823f0889d1720ab68fa4f1e7c721cee # v0.1.0
  with:
    receipts: samples/sample-bakery-receipts/receipts.json
    public-keys-file: samples/sample-bakery-receipts/ed25519.pem
```

From an agent, with the `verify_receipt` tool of the
[verifiable-receipts MCP server](https://github.com/christiaan839-beep/verifiable-receipts/tree/main/packages/verifiable-receipts-py#mcp-server),
or the [verify-receipts Agent Skill](https://github.com/christiaan839-beep/verifiable-receipts/tree/main/skills/verify-receipts).

From Python, with the verifiable-receipts verifier:

```python
import json
from pathlib import Path
from sovereign_matrix.verifiable_receipts import verify_v2_receipt

folder = Path("samples/sample-bakery-receipts")
key = (folder / "ed25519.pem").read_bytes()
for receipt in json.loads((folder / "receipts.json").read_text()):
    print(receipt["id"], verify_v2_receipt(receipt, key))
```
