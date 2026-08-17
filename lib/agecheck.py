"""frappe-nix-agecheck — verify .age files are encrypted to their declared recipients.

An age file's header lists one stanza per recipient, in ASCII, before the
`---` line. For an SSH recipient the stanza carries a tag that is
`b64_nopad(sha256(<ssh wire blob>)[:4])` — derivable from the *public* key
alone. So the whole check runs offline, with no identity and no decryption.

This exists because the alternative failed in practice: a rules file can be
edited without re-encrypting the secrets, and nothing notices. In the repo this
tool was written for, a rotated key sat in `secrets.nix` for months while the
`.age` file was still encrypted to the key it replaced — the person it was
rotated *for* could not decrypt anything, and there was no signal.

Modes:
  check <rules.json> [--root DIR]   compare declared recipients to ciphertext
  explain <rules.json> <relpath>    why can't *I* decrypt this?
  tag <'ssh-ed25519 AAAA…'>         print one recipient tag
"""

import base64
import hashlib
import json
import os
import re
import sys

SSH_TYPES = ("ssh-ed25519", "ssh-rsa")
STANZA = re.compile(r"^-> (\S+)(?: (\S+))?")


def tag(pubkey: str) -> str | None:
    """age's SSH recipient tag, or None for a key type that carries no tag."""
    parts = pubkey.split()
    if len(parts) < 2 or parts[0] not in SSH_TYPES:
        return None
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except Exception:
        return None
    return base64.b64encode(hashlib.sha256(blob).digest()[:4]).decode().rstrip("=")


def label(pubkey: str) -> str:
    """Trailing comment if the key has one, else a short fingerprint."""
    parts = pubkey.split()
    if len(parts) >= 3:
        return " ".join(parts[2:])
    return (parts[1][:12] + "…") if len(parts) > 1 else pubkey


def header_tags(path: str) -> tuple[set[str], list[str]]:
    """(SSH stanza tags, other *recognised* stanza types).

    Unknown stanza types are ignored rather than reported. age implementations
    deliberately emit "grease" — a stanza with a random type — so that parsers
    cannot ossify around the types that exist today. rage does this on every
    encryption, so treating unknown types as something to mention would put a
    spurious note on every single file. We only care whether the declared SSH
    recipients are present; anything we cannot attribute is not evidence of
    anything.
    """
    tags: set[str] = set()
    known_other: list[str] = []
    with open(path, "rb") as fh:
        for raw in fh:
            line = raw.decode("ascii", "replace").rstrip("\r\n")
            if line.startswith("---"):
                break
            m = STANZA.match(line)
            if not m:
                continue
            kind, value = m.group(1), m.group(2)
            if kind in SSH_TYPES and value:
                tags.add(value)
            elif kind in ("X25519", "scrypt"):
                known_other.append(kind)
    return tags, known_other


def load_rules(path: str) -> dict[str, list[str]]:
    with open(path) as fh:
        return json.load(fh)


def cmd_check(rules_path: str, root: str) -> int:
    rules = load_rules(rules_path)
    problems: list[str] = []
    notes: list[str] = []

    for rel, recipients in sorted(rules.items()):
        full = os.path.join(root, rel)
        if not os.path.exists(full):
            problems.append(
                f"{rel}: declared in flake.nix but missing.\n"
                f"    Create it with:  edit-secret {stem(rel)}"
            )
            continue

        present, opaque = header_tags(full)
        expected = {}
        untaggable = []
        for key in recipients:
            t = tag(key)
            if t is None:
                untaggable.append(key)
            else:
                expected[t] = key

        missing = [expected[t] for t in expected if t not in present]
        stale = present - set(expected)

        if missing:
            problems.append(
                f"{rel}: not encrypted to {len(missing)} declared recipient(s):\n"
                + "".join(f"      {tag(k)}  {label(k)}\n" for k in missing)
                + "    Someone who can still decrypt it must run:  rekey-secrets"
            )
        if stale:
            notes.append(
                f"{rel}: encrypted to {len(stale)} key(s) no longer declared "
                f"({', '.join(sorted(stale))}) — they can still read it. "
                f"Run rekey-secrets to drop them."
            )
        if opaque:
            notes.append(
                f"{rel}: {len(opaque)} {'/'.join(sorted(set(opaque)))} recipient stanza(s) carry no "
                f"identifying tag and were not verified."
            )
        for key in untaggable:
            notes.append(
                f"{rel}: declared recipient {label(key)!r} is not an SSH key, so its presence "
                f"in this file cannot be verified."
            )

    # An .age file with no rule is dead weight at best and a stale copy of a
    # renamed secret at worst.
    declared = {os.path.normpath(r) for r in rules}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".age"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if os.path.normpath(rel) not in declared:
                notes.append(f"{rel}: on disk but not declared in flake.nix — orphaned?")

    for n in notes:
        print(f"note: {n}", file=sys.stderr)

    if problems:
        print("\nfrappe-nix: secrets are out of sync with their declared recipients\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}\n", file=sys.stderr)
        return 1

    print(f"frappe-nix: {len(rules)} secret(s) encrypted to their declared recipients.")
    return 0


def stem(rel: str) -> str:
    """secrets/erp.example.com/db-password.age -> erp.example.com/db-password"""
    parts = rel.split(os.sep)
    return os.sep.join(parts[1:])[: -len(".age")] if len(parts) > 1 else rel[: -len(".age")]


def own_keys() -> list[tuple[str, str, str]]:
    """(path, tag, pubkey) for every readable ~/.ssh/*.pub."""
    found = []
    ssh = os.path.expanduser("~/.ssh")
    if not os.path.isdir(ssh):
        return found
    for name in sorted(os.listdir(ssh)):
        if not name.endswith(".pub"):
            continue
        path = os.path.join(ssh, name)
        try:
            with open(path) as fh:
                line = fh.read().strip()
        except OSError:
            continue
        t = tag(line)
        if t:
            found.append((path, t, line))
    return found


def cmd_explain(rules_path: str, rel: str, root: str) -> int:
    rules = load_rules(rules_path)
    if rel not in rules:
        # Accept a bare name too: `explain backup-access`
        matches = [r for r in rules if stem(r) == rel or os.path.basename(r) == f"{rel}.age"]
        if len(matches) != 1:
            print(f"no such secret: {rel}", file=sys.stderr)
            print("declared:", file=sys.stderr)
            for r in sorted(rules):
                print(f"  {stem(r)}", file=sys.stderr)
            return 2
        rel = matches[0]

    full = os.path.join(root, rel)
    print(f"\n  {rel}\n")

    mine = own_keys()
    print("  Your public keys:")
    if not mine:
        print("    (none found in ~/.ssh/*.pub)")
    for path, t, _ in mine:
        print(f"    {t}  {path}")

    if not os.path.exists(full):
        print(f"\n  The file does not exist. Create it with:  edit-secret {stem(rel)}\n")
        return 1

    present, opaque = header_tags(full)
    declared = {tag(k): k for k in rules[rel] if tag(k)}

    print("\n  Recipients of this file:")
    for t in sorted(present):
        who = label(declared[t]) if t in declared else "— not declared in flake.nix —"
        print(f"    {t}  {who}")
    for kind in sorted(set(opaque)):
        print(f"    (a {kind} recipient, which carries no tag)")

    undeclared_missing = [k for t, k in declared.items() if t not in present]
    if undeclared_missing:
        print("\n  Declared in flake.nix but NOT encrypted into this file:")
        for k in undeclared_missing:
            print(f"    {tag(k)}  {label(k)}")
        print("\n  → someone changed the recipient list without re-encrypting.")
        print("    Anyone who can still decrypt must run:  rekey-secrets && git commit")
        return 1

    if any(t in present for _, t, _ in mine):
        print("\n  One of your keys is a recipient — decryption should work.")
        print("  If it does not, check that agenix-shell's identityPaths point at the")
        print("  matching *private* key, and that it is readable.\n")
        return 0

    print("\n  None of your keys is a recipient of this file.")
    print("  Ask someone listed above to add yours: put the public key in")
    print("  `frappe-nix.secrets.recipients` in flake.nix, then `rekey-secrets`.\n")
    return 1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    mode, rest = argv[1], argv[2:]
    root = os.environ.get("FRAPPE_BENCH_ROOT", ".")
    if "--root" in rest:
        i = rest.index("--root")
        root = rest[i + 1]
        rest = rest[:i] + rest[i + 2 :]

    if mode == "tag":
        t = tag(rest[0])
        if t is None:
            print("not an SSH public key", file=sys.stderr)
            return 2
        print(t)
        return 0
    if mode == "check":
        return cmd_check(rest[0], root)
    if mode == "explain":
        return cmd_explain(rest[0], rest[1], root)

    print(f"unknown mode: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
