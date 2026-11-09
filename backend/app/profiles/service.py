"""Resolve whatever the client sent (preset id, ``p`` param, partial object) into a full Profile."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.errors import ValidationFailed
from app.modelcfg import ModelConfig
from app.profiles.codec import decode_param, encode_param
from app.profiles.contract import (
    PROFILE_SCHEMA_VERSION,
    Profile,
    ProfileFilters,
    ProfileInput,
)


def _legacy_shape(data: dict[str, Any]) -> dict[str, Any]:
    """Accept the early draft of section 3.2 ({"v":1,"profile":"pensioner","mobility":"no_car"}):
    ``profile`` -> ``preset`` and top-level filter keys -> ``filters``."""
    filter_keys = set(ProfileFilters.model_fields)
    if "profile" not in data and not filter_keys & set(data):
        return data
    out = dict(data)
    if "profile" in out and "preset" not in out:
        out["preset"] = out.pop("profile")
    moved = {k: out.pop(k) for k in list(out) if k in filter_keys}
    if moved:
        out["filters"] = {**moved, **(out.get("filters") or {})}
    return out


def _errors(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {"loc": [str(x) for x in e.get("loc", ())], "msg": str(e.get("msg", "")), "type": e.get("type", "")}
        for e in exc.errors()
    ]


class PresetView(BaseModel):
    id: str
    name: str
    description: str
    key_thresholds: str
    profile: Profile


class ProfileResolver:
    def __init__(self, model_cfg: ModelConfig, *, default_preset: str, max_param_len: int = 2000) -> None:
        if default_preset not in model_cfg.presets:
            default_preset = next(iter(model_cfg.presets))
        self.cfg = model_cfg
        self.default_preset = default_preset
        self.max_param_len = max_param_len
        self._presets = {pid: self._build_preset(pid) for pid in model_cfg.presets}

    def _build_preset(self, pid: str) -> Profile:
        p = self.cfg.presets[pid]
        return Profile(v=PROFILE_SCHEMA_VERSION, preset=pid, weights=p.weights, filters=ProfileFilters(**p.filters))

    # --- presets ---------------------------------------------------------------
    def presets(self) -> list[PresetView]:
        return [
            PresetView(
                id=pid,
                name=cfg.name,
                description=cfg.description,
                key_thresholds=cfg.key_thresholds,
                profile=self._presets[pid],
            )
            for pid, cfg in self.cfg.presets.items()
        ]

    def preset(self, pid: str) -> Profile:
        try:
            return self._presets[pid]
        except KeyError:
            raise ValidationFailed(
                f"Неизвестный пресет профиля «{pid}»",
                code="profile_unknown_preset",
                details={"presets": list(self._presets)},
            ) from None

    def default(self) -> Profile:
        return self._presets[self.default_preset]

    def pristine_preset_id(self, profile: Profile) -> str | None:
        """Preset id if the profile is exactly that preset (used for index_result.profile_id)."""
        if profile.preset and profile.preset in self._presets:
            if self._presets[profile.preset].hash == profile.hash:
                return profile.preset
        return None

    # --- resolution ------------------------------------------------------------
    def resolve(self, ref: str | dict[str, Any] | ProfileInput | Profile | None) -> Profile:
        if ref is None:
            return self.default()
        if isinstance(ref, Profile):
            return ref
        if isinstance(ref, ProfileInput):
            return self._from_input(ref)
        if isinstance(ref, str):
            if ref in self._presets:
                return self._presets[ref]
            return self.resolve(decode_param(ref, max_len=self.max_param_len))
        if isinstance(ref, dict):
            ref = _legacy_shape(ref)
            version = ref.get("v", PROFILE_SCHEMA_VERSION)
            if version != PROFILE_SCHEMA_VERSION:
                raise ValidationFailed(
                    f"Версия профиля {version!r} не поддерживается",
                    code="profile_version_unsupported",
                    details={"supported": [PROFILE_SCHEMA_VERSION]},
                )
            try:
                inp = ProfileInput.model_validate(ref)
            except ValidationError as exc:
                raise ValidationFailed("Некорректный профиль", code="profile_invalid", details=_errors(exc)) from exc
            return self._from_input(inp)
        raise ValidationFailed("Некорректный профиль", code="profile_invalid")

    def _from_input(self, inp: ProfileInput) -> Profile:
        if inp.v != PROFILE_SCHEMA_VERSION:
            raise ValidationFailed(
                f"Версия профиля {inp.v!r} не поддерживается",
                code="profile_version_unsupported",
                details={"supported": [PROFILE_SCHEMA_VERSION]},
            )
        if inp.preset is not None:
            self.preset(inp.preset)  # validates the id
        base_id = inp.preset or (self.default_preset if inp.weights is None else None)
        base = self.cfg.presets[base_id] if base_id else None
        weights = inp.weights if inp.weights is not None else base.weights  # type: ignore[union-attr]
        filters = {**(base.filters if base else {}), **(inp.filters or {})}
        label = inp.preset if inp.preset else (base_id if inp.weights is None and not inp.filters else None)
        try:
            return Profile(v=PROFILE_SCHEMA_VERSION, preset=label, weights=weights, filters=ProfileFilters(**filters))
        except ValidationError as exc:
            raise ValidationFailed("Некорректный профиль", code="profile_invalid", details=_errors(exc)) from exc

    def from_query(self, profile: str | None, p: str | None) -> Profile:
        """GET-style resolution: ``p`` (serialized profile) wins over ``profile`` (preset id)."""
        if p:
            return self.resolve(decode_param(p, max_len=self.max_param_len))
        if profile:
            return self.preset(profile)
        return self.default()

    def encode(self, profile: Profile, *, compress: bool = True) -> str:
        return encode_param(profile.canonical_dict(), compress=compress)
