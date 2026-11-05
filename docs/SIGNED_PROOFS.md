# Message and file signing

Available in **0.5.0-beta.3** under **Wallet → My addresses → Sign / Verify**.
Signing and verification run locally in Full node and Lite modes. Signing
requires an unlocked profile and approval of the exact message or file digest;
public verification needs neither an unlocked wallet nor a running node.

New v2 proofs use Warthog Python SDK 0.4.0 at commit
`bb1a67ab91ac69c976e2652f545adcbef9d0bde8`, through native coincurve/libsecp256k1.
The framed `.wartproof` document is a WartNode application format, not an
existing Warthog network message-signing standard. Earlier v1 prototype proofs
remain readable; new signing always creates v2.

## Using the tool

1. Choose an owned address and **Message** or **File**. For a wallet claim, paste
   the service's complete unique challenge, including its name, intended action,
   requested username, address, nonce and expiry, if supplied.
2. Select **Review and sign**. Unlock the profile if necessary, inspect the exact
   content, and approve. Cancelling, locking the profile, switching addresses or
   letting the two-minute review expire invalidates the approval.
3. Copy the signature document or save it as `.wartproof`. A message document
   includes the entire message. A file document includes its original basename,
   byte length and SHA-256 digest; share the original file separately.
4. On **Verify**, independently enter the address you expect, then paste or open
   the signature document. For a file proof, also choose the original file.
   Verification checks the key/address association, signature, expected address,
   and actual file contents. Editing an input immediately clears the old verdict.

A file can be renamed without invalidating its contents. Changing the original
filename *inside the proof* invalidates the signature, because that metadata is
signed too. Files are streamed on a cancellable worker in 1 MiB chunks and are
limited to 16 GiB; messages are limited to 16,384 UTF-8 bytes. No content is uploaded.

Files are signed as the byte snapshot read for the review. The review displays its
digest and size. A later change to the file requires another signing operation;
verification will reject a changed file. Concurrent changes detected during the
read are rejected. This is not a filesystem snapshot or a trusted timestamp.

## Public v2 wire format

### Examples and wallet claims

Complete signed examples are provided for a
[message](../tests/vectors/message-v2.wartproof) and an
[empty file](../tests/vectors/file-v2.wartproof). They use the public test-only
private scalar `1`; never send funds to their address. The original file for
the file example is zero bytes. The earlier
[v1 message](../tests/vectors/message-v1.wartproof) is verification-only.

A claiming service can issue text such as:

```text
Service: example.com
Action: claim wallet name
Username: chosen-name
Address: ADDRESS_TO_CLAIM
Nonce: SERVER_GENERATED_RANDOM_SINGLE_USE_VALUE
Expires: SERVER_SELECTED_UTC_TIMESTAMP
```

The service must compare the complete message with its stored challenge,
verify the expected address, enforce expiry and consume the nonce once.
Example text is a template, not a reusable challenge. This desktop does not
create an explorer name record or provide a website login bridge. Its framed
proof is not a raw SDK signature for an existing metadata-service challenge.

### Document fields

The UTF-8 JSON document has exactly these top-level properties:

| Property | Value |
| --- | --- |
| `format` | `"wartnode-signed-proof"` |
| `version` | Integer `2` |
| `network` | `"warthog-mainnet"` |
| `kind` | `"message"` or `"file"` |
| `address` | 48 lowercase hexadecimal characters, including the checksum |
| `publicKey` | Compressed secp256k1 public key, 33 bytes as lowercase hex |
| `content` | One of the exact objects below |
| `signature` | 65 bytes as 130 lowercase hex characters: 32-byte big-endian `r`, then `s`, then one-byte recovery ID |

Message content is `{"message":"exact text"}`. Text is preserved without Unicode
normalization, trimming or newline conversion by the protocol. The editor uses
Qt's normal text editing behavior; the approval dialog shows what will be signed.
Unicode control characters below U+0020 are rejected except CR, LF and TAB.

File content is `{"name":"basename.ext","size":123,"sha256":"64 lowercase hex characters"}`.
`size` is an integer between zero and 17,179,869,184. The filename contains 1–255
Unicode characters, no folder separator and no character below U+0020. `sha256`
covers every byte of the original file, including empty files.

Unknown properties, duplicate JSON keys, unsupported versions/networks/types,
invalid Unicode, invalid public points and malformed/non-normalized signatures
are rejected. Documents are limited to 128 KiB. A JSON boolean is not an integer.

### Signed bytes and verification

1. Remove only `signature` from the document. All other fields are signed.
2. Serialize that body as compact JSON, sorting property names lexicographically
   at every level, using UTF-8 with unescaped non-ASCII characters. No whitespace
   appears outside strings. Use JSON's standard escapes inside strings, including
   `\n`, `\r`, `\t`, `\"` and `\\`. There are no floating-point numbers in this format.
3. Let `J` be those bytes. The exact signing preimage is:

   ```text
   00 || UTF8("WartNode Signed Proof") || 00 || UTF8("v2") || 00
      || uint32_big_endian(byte_length(J)) || J
   ```

4. Pass the complete preimage to `Account.sign_bytes()` **without pre-hashing**.
   The SDK hashes it once with SHA-256 and signs with secp256k1 ECDSA using
   deterministic RFC 6979 nonces. The SDK returns low-s `r || s || recid` and
   adjusts the recovery ID if normalization changes `s`. The curve order is:

   ```text
   n = FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
   ```

   Before calling recovery, require exactly 130 lowercase hexadecimal characters,
   `1 <= r < n`, `1 <= s <= floor(n/2)`, and recovery ID 0–3. Whitespace, uppercase
   encodings, DER encodings and Ethereum-style recovery values 27/28 are rejected.
5. Derive the Warthog address: `A = RIPEMD160(SHA256(compressedPublicKey))`, then
   append `SHA256(A)[0:4]` and encode the 24 bytes as lowercase hex. Require that
   it equals both the signed address and the independently expected address.
6. Recover the compressed key with `Account.recover_public_key(preimage, signature)`
   and require exact equality with the signed `publicKey`. Successful recovery
   alone is insufficient: altered content can recover another valid key. The key
   must belong to both the declared and independently expected address. For a file
   proof, also compute SHA-256 and length of the supplied original file and require
   both to match. Do not require its current basename to match the signed basename.

JSON formatting and property order in the exported document can change, because
verification reconstructs the canonical body. Changes to signed values cannot.

`tests/vectors/message-v2.wartproof` is a deterministic interoperability vector
using the publicly known **test-only** private scalar `1`. Its address must never
be funded. The tests also verify the signature with the independent `ecdsa`
library. The qualification harness also exchanges complete message/file proof
preimages with the pinned TypeScript SDK in both directions.

### Reading earlier v1 documents

V1 has the same strict schema, with integer `version: 1`, a `v1` domain marker,
and a 64-byte `r || s` signature without a recovery ID. It is verified against
the explicit public key using cryptography/OpenSSL; all content, low-s,
address and expected-identity checks still apply. The unchanged
`tests/vectors/message-v1.wartproof` checks compatibility. Changing a v2 version
field to 1 or stripping its recovery byte cannot convert it into a valid v1 proof,
because the version and domain are signed. The helper never creates v1 proofs.

## Verification without the desktop

The verifier module imports neither Qt nor wallet/profile code. A source checkout
with the existing dependencies can run:

```powershell
.\.venv\Scripts\python.exe scripts\verify-proof.py claim.wartproof --address EXPECTED_ADDRESS
.\.venv\Scripts\python.exe scripts\verify-proof.py release.wartproof --address EXPECTED_ADDRESS --file release.zip
```

Exit status is `0` on successful verification and `1` on invalid proofs or files.
The message is printed as untrusted signed text after the verification result.
Websites can implement the published format independently; explorer naming and
challenge issuance still need integration in that service.

## Authorization boundaries and meaning

The private helper constructs the complete domain-separated payload itself. It
does not expose an arbitrary hash-signing operation. Only it decrypts an owned
address's key. Approval tokens are single use, expire after two minutes, and are
invalidated by profile locks and connection/address changes. File bytes never go
through the private-helper pipe. Proof reviews are separate from payment reviews.
SDK account objects stay inside the helper and are never returned, serialized or
logged. SDK exceptions are replaced by controlled public errors. Python does not
guarantee erasure of temporary key copies from process memory.

A valid proof establishes that someone with access to the corresponding private
key signed that content. It establishes neither legal identity, exclusive/current
key custody, a trusted creation time, nor safety of a signed file. Fresh single-use
challenges and server-side expiry checks are the claiming service's responsibility.
No address-name record is written on chain, and there is no HTTP signing service
or listening port.
