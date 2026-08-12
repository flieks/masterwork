"""Asset business logic: scan providers, search, and validated file writes."""

from __future__ import annotations

from collections.abc import Iterable

from app.api.v1.assets.schemas import AssetDetail, AssetKind, AssetSummary
from app.core.exceptions import AssetNotFoundError, InvalidAssetIdError, ReadOnlyAssetError
from app.providers.base import Provider, ScannedAsset, resolve_within_roots
from app.services.asset_history import prepare_snapshots, snapshot_writes


def parse_asset_id(asset_id: str) -> tuple[str, str, str]:
    # maxsplit keeps colons inside the name (plugin skills: "vercel:bootstrap").
    parts = asset_id.split(":", 2)
    if len(parts) != 3 or not all(parts):
        raise InvalidAssetIdError(f"malformed asset id: {asset_id!r}")
    provider, kind, name = parts
    return provider, kind, name


def _to_summary(asset: ScannedAsset) -> AssetSummary:
    return AssetSummary(
        id=asset.id,
        kind=AssetKind(asset.kind),
        provider=asset.provider,
        name=asset.name,
        title=asset.title,
        description=asset.description,
        model=asset.model,
        path=str(asset.path),
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        read_only=asset.read_only,
    )


def _to_detail(asset: ScannedAsset) -> AssetDetail:
    return AssetDetail(**_to_summary(asset).model_dump(), content=asset.content)


def _scan_all(providers: Iterable[Provider]) -> list[ScannedAsset]:
    assets: list[ScannedAsset] = []
    for provider in providers:
        assets.extend(provider.scan())
    return assets


def _matches_query(asset: ScannedAsset, q: str) -> bool:
    needle = q.casefold()
    haystacks = (asset.name, asset.title, asset.description, asset.content)
    return any(needle in field.casefold() for field in haystacks)


def list_assets(
    providers: Iterable[Provider],
    *,
    kind: AssetKind | None = None,
    q: str | None = None,
) -> list[AssetSummary]:
    assets = _scan_all(providers)
    if kind is not None:
        assets = [a for a in assets if a.kind == kind.value]
    if q:
        assets = [a for a in assets if _matches_query(a, q)]
    return [_to_summary(a) for a in assets]


def find_asset(providers: Iterable[Provider], asset_id: str) -> ScannedAsset:
    """Return the asset with this id, or raise (400 malformed / 404 unknown)."""
    parse_asset_id(asset_id)  # 400 on malformed
    for asset in _scan_all(providers):
        if asset.id == asset_id:
            return asset
    raise AssetNotFoundError(f"unknown asset: {asset_id}")


def existing_asset_ids(providers: Iterable[Provider]) -> set[str]:
    """Every asset id currently on disk across all providers."""
    return {asset.id for asset in _scan_all(providers)}


def get_asset(providers: Iterable[Provider], asset_id: str) -> AssetDetail:
    return _to_detail(find_asset(providers, asset_id))


async def update_asset(providers: list[Provider], asset_id: str, content: str) -> AssetDetail:
    asset = find_asset(providers, asset_id)
    if asset.read_only:
        raise ReadOnlyAssetError(
            f"{asset_id} is read-only and cannot be edited through the API; "
            "a plugin asset is managed by its marketplace, a role config on disk"
        )
    roots = [root for provider in providers for root in provider.roots()]
    resolved = resolve_within_roots(asset.path, roots)
    if resolved is None:
        # Should never happen for a scanned asset, but never write outside a root.
        raise InvalidAssetIdError(f"asset path is outside provider roots: {asset.path}")
    # Snapshotted like an accepted proposal: an unrecorded manual edit would
    # also poison the next proposal's diff, which would then show both changes.
    await prepare_snapshots(providers, [resolved])
    resolved.write_text(content, encoding="utf-8")
    await snapshot_writes(providers, [resolved], f"masterwork: edit asset: {asset_id}")
    return get_asset(providers, asset_id)
