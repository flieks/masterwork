"""Turn a recorded asset use — a kind, a name, and which agent ran — into the id
of the asset that exists on disk.

A session records only what it saw: `skill` + `deploy`, or `agent` + `reviewer`.
The same name can live in several stores (a Claude skill, the generic folder, a
Codex plugin), and a skill migrated to the generic folder changes id without
the recorded name changing at all. So ids are resolved when read, against the
providers, preferring the stores the recording agent actually loads from; a
stored id would go stale the moment a skill moved.

Listing ids needs no file content, so the index is built from each provider's
`asset_refs()` (falling back to a full scan for a provider without one), once
per request and only if a row asks.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app.db.models.coding import ASSET_SKILL, SOURCE_CLAUDE_CODE, SOURCE_CODEX
from app.providers.base import AssetRef, IndexedProvider, Provider

# Where each agent looks, most specific first; the rest are a last resort, so a
# use still links to a same-named asset another agent owns.
_PREFERENCE: dict[tuple[bool, str], tuple[str, ...]] = {
    (True, "skill"): ("codex", "generic", "codex-plugin", "claude", "claude-plugin"),
    (True, "agent"): ("codex", "claude", "claude-plugin", "masterwork"),
    (False, "skill"): ("claude", "generic", "claude-plugin", "codex", "codex-plugin"),
    (False, "agent"): ("claude", "claude-plugin", "codex", "masterwork"),
}


@dataclass(frozen=True)
class ResolvedAssetId:
    asset_id: str
    # False when nothing on disk has that kind and name; the id is then the
    # recording agent's own form, which links to an asset page that 404s.
    found: bool


class AssetIdResolver:
    def __init__(self, providers: Iterable[Provider]) -> None:
        self._providers = list(providers)
        self._index: dict[tuple[str, str], dict[str, str]] | None = None

    def _build(self) -> dict[tuple[str, str], dict[str, str]]:
        index: dict[tuple[str, str], dict[str, str]] = {}
        for ref in _refs(self._providers):
            index.setdefault((ref.kind, ref.name), {}).setdefault(ref.provider, ref.id)
        return index

    def resolve(self, kind: str, name: str, source: str | None) -> ResolvedAssetId:
        codex = source == SOURCE_CODEX
        if self._index is None:
            self._index = self._build()
        by_provider = self._index.get((kind, name), {})
        for provider in _PREFERENCE.get((codex, kind), ()):
            if provider in by_provider:
                return ResolvedAssetId(by_provider[provider], True)
        if by_provider:  # a store no preference names (a future provider)
            return ResolvedAssetId(by_provider[min(by_provider)], True)
        fallback = "codex" if codex else "claude"
        return ResolvedAssetId(f"{fallback}:{kind}:{name}", False)

    def sources_for(self, asset_id: str, kind: str, name: str) -> tuple[str, ...] | None:
        """Which recording agents' uses of (kind, name) resolve to `asset_id`, or
        None when every agent's do (or the asset is gone) and nothing needs filtering."""
        matching = tuple(
            source
            for source in (SOURCE_CLAUDE_CODE, SOURCE_CODEX)
            if self.resolve(kind, name, source) == ResolvedAssetId(asset_id, True)
        )
        return None if len(matching) != 1 else matching


# The stores Codex loads skills from, and so the names a `$name` mention can mean.
CODEX_SKILL_PROVIDERS = frozenset({"codex", "generic", "codex-plugin"})


def codex_skill_names(providers: Iterable[Provider]) -> Callable[[], frozenset[str]]:
    """A lazy, memoized lookup of every skill name Codex could load — built only
    if a prompt actually contains a `$`, since ingest runs on every hook."""
    providers = [p for p in providers if p.name in CODEX_SKILL_PROVIDERS]
    found: list[frozenset[str]] = []

    def names() -> frozenset[str]:
        if not found:
            found.append(frozenset(ref.name for ref in _refs(providers) if ref.kind == ASSET_SKILL))
        return found[0]

    return names


def _refs(providers: Iterable[Provider]) -> Iterable[AssetRef]:
    for provider in providers:
        if isinstance(provider, IndexedProvider):
            yield from provider.asset_refs()
        else:
            yield from (AssetRef(a.provider, a.kind, a.name) for a in provider.scan())
