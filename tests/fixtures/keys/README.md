# tests/fixtures/keys

Throwaway keypairs for the offline secrets tests. **Committed on purpose, and
useless outside them** — they exist so `edit-secret` and `rekey-secrets` can be
driven against a real `ragenix` in a sandbox with no network and no key of the
builder's.

Generated once and never rotated: the tests assert against the recipient tags
derived from these exact public keys.
