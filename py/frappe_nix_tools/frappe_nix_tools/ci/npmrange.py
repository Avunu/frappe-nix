"""Whether a version satisfies an npm range, the way node-semver's ``satisfies`` decides it.

``lockcheck.yarn_problems`` asks this of every new or changed yarn entry: the version it locks
must be one its spec's range allows, else a person looks. It covers what yarn (classic) lock
keys hold for a registry package: ``||`` sets of comparators (``<``, ``<=``, ``>``, ``>=``,
``=``), hyphen ranges, x-ranges (``1.x``, ``1.2.*``, ``*``, ``""``), tilde (``~``, ``~>``) and
caret ranges, partial versions, and node-semver's prerelease rule (a prerelease satisfies a set
only when one of its comparators names a prerelease of the same ``major.minor.patch``).
Anything else (a dist-tag such as ``latest``, a URL, ``git``, ``file:``, ``link:`` or
``workspace:`` spec, a loose or invalid version) is ``None``: not read, so the caller treats it
as a range this check cannot vouch for.
"""

import re
from typing import NamedTuple

_NUM = r"0|[1-9]\d*"
_X = rf"{_NUM}|[xX*]"
_PRE_ID = r"(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
_PRE = rf"(?:-(?P<pre>{_PRE_ID}(?:\.{_PRE_ID})*))?"
_BUILD = r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
_VERSION = re.compile(rf"v?(?P<major>{_NUM})\.(?P<minor>{_NUM})\.(?P<patch>{_NUM}){_PRE}{_BUILD}")
_PARTIAL = re.compile(rf"v?=?\s*(?P<major>{_X})(?:\.(?P<minor>{_X})(?:\.(?P<patch>{_X}){_PRE}{_BUILD})?)?")
_COMPARATOR = re.compile(r"(?P<op><=|>=|<|>|=|~>|~|\^)?\s*(?P<v>\S+)")
_HYPHEN = re.compile(r"^\s*(?P<a>\S+)\s+-\s+(?P<b>\S+)\s*$")


class Version(NamedTuple):
	major: int
	minor: int
	patch: int
	pre: tuple[int | str, ...] = ()

	def key(self) -> tuple:
		# A version with no prerelease sorts above all of its prereleases; a numeric identifier
		# sorts below an alphanumeric one, and numbers compare as numbers.
		pre = tuple((0, p, "") if isinstance(p, int) else (1, 0, p) for p in self.pre)
		return (self.major, self.minor, self.patch, 0 if self.pre else 1, pre)


def _pre(text: str | None) -> tuple[int | str, ...]:
	if not text:
		return ()
	return tuple(int(p) if p.isdigit() else p for p in text.split("."))


def parse(text: str) -> Version | None:
	"""``text`` as a full version (a leading ``v`` and build metadata allowed), else ``None``."""
	m = _VERSION.fullmatch(text.strip())
	if not m:
		return None
	return Version(int(m["major"]), int(m["minor"]), int(m["patch"]), _pre(m["pre"]))


class _Cmp(NamedTuple):
	op: str  # "<", "<=", ">", ">=", "="
	v: Version

	def test(self, v: Version) -> bool:
		a, b = v.key(), self.v.key()
		return {
			"<": a < b,
			"<=": a <= b,
			">": a > b,
			">=": a >= b,
			"=": a == b,
		}[self.op]


def _x(part: str | None) -> bool:
	return part is None or part in ("x", "X", "*")


def _expand(op: str, text: str) -> list[_Cmp] | None:
	"""One comparator (operator and partial version) as plain comparators; ``[]`` is any."""
	m = _PARTIAL.fullmatch(text)
	if not m:
		return None
	ma, mi, pa = m["major"], m["minor"], m["patch"]
	pre = _pre(m["pre"])
	if _x(ma):
		return [_Cmp("<", Version(0, 0, 0, (0,)))] if op in ("<", ">") else []
	M = int(ma)
	if op in ("~", "~>"):
		if _x(mi):
			return [_Cmp(">=", Version(M, 0, 0)), _Cmp("<", Version(M + 1, 0, 0, (0,)))]
		n = int(mi)
		lo = Version(M, n, 0 if _x(pa) else int(pa), () if _x(pa) else pre)
		return [_Cmp(">=", lo), _Cmp("<", Version(M, n + 1, 0, (0,)))]
	if op == "^":
		if _x(mi):
			return [_Cmp(">=", Version(M, 0, 0)), _Cmp("<", Version(M + 1, 0, 0, (0,)))]
		n = int(mi)
		if _x(pa):
			lo = Version(M, n, 0)
			hi = Version(M + 1, 0, 0, (0,)) if M else Version(0, n + 1, 0, (0,))
			return [_Cmp(">=", lo), _Cmp("<", hi)]
		p = int(pa)
		lo = Version(M, n, p, pre)
		if M:
			hi = Version(M + 1, 0, 0, (0,))
		elif n:
			hi = Version(0, n + 1, 0, (0,))
		else:
			hi = Version(0, 0, p + 1, (0,))
		return [_Cmp(">=", lo), _Cmp("<", hi)]
	if _x(mi) or _x(pa):
		# An x-range: 1.x, 1.2.x, with or without an operator.
		n = 0 if _x(mi) else int(mi)
		lo = Version(M, n, 0)
		hi = Version(M + 1, 0, 0, (0,)) if _x(mi) else Version(M, n + 1, 0, (0,))
		if op in ("", "="):
			return [_Cmp(">=", lo), _Cmp("<", hi)]
		if op == ">":
			return [_Cmp(">=", Version(hi.major, hi.minor, hi.patch))]
		if op == ">=":
			return [_Cmp(">=", lo)]
		if op == "<":
			return [_Cmp("<", Version(M, n, 0, (0,)))]
		return [_Cmp("<", hi)]  # "<="
	return [_Cmp(op or "=", Version(M, int(mi), int(pa), pre))]


def _hyphen(a: str, b: str) -> list[_Cmp] | None:
	lo = _PARTIAL.fullmatch(a)
	hi = _PARTIAL.fullmatch(b)
	if not lo or not hi:
		return None
	out: list[_Cmp] = []
	if not _x(lo["major"]):
		low = _expand(">=", a)
		if low is None:
			return None
		out += low
	if not _x(hi["major"]):
		high = _expand("<=", b)  # a partial upper end is an x-range: 1.2.3 - 2.3 is <2.4.0-0
		if high is None:
			return None
		out += high
	return out


def _set(text: str) -> list[_Cmp] | None:
	if m := _HYPHEN.match(text):
		return _hyphen(m["a"], m["b"])
	# An operator may stand apart from its version (">= 1.2.3"): join them first.
	text = re.sub(r"(<=|>=|<|>|=|~>|~|\^)\s+", r"\1", text.strip())
	out: list[_Cmp] = []
	for part in text.split():
		m = _COMPARATOR.fullmatch(part)
		if not m:
			return None
		cmps = _expand(m["op"] or "", m["v"])
		if cmps is None:
			return None
		out += cmps
	# node-semver reads >=0.0.0 as any version (*), which a range of several sets then is.
	return [c for c in out if c != _Cmp(">=", Version(0, 0, 0))]


def _test_set(cmps: list[_Cmp], v: Version) -> bool:
	if not all(c.test(v) for c in cmps):
		return False
	if not v.pre:
		return True
	# node-semver: a prerelease only where a comparator names one of the same major.minor.patch.
	return any(c.v.pre and c.v[:3] == v[:3] for c in cmps)


def satisfies(version: str, rng: str) -> bool | None:
	"""Whether ``version`` is in the npm range ``rng``; ``None`` when either is not one this
	module reads (a dist-tag, URL or other non-semver spec, or a version that is not semver)."""
	v = parse(version)
	if v is None or len(rng) > 1024:
		return None
	sets = []
	for alt in rng.split("||"):
		cmps = _set(alt)
		if cmps is None:
			return None
		sets.append(cmps)
	if len(sets) > 1 and [] in sets:
		sets = [[]]  # node-semver: a set that is any version (*) stands for the whole range
	return any(_test_set(cmps, v) for cmps in sets)
