import base64
import json
import zlib

import pytest

from app.core.config import BACKEND_DIR
from app.core.errors import ValidationFailed
from app.modelcfg import load_model_config
from app.profiles.codec import decode_param, encode_param
from app.profiles.contract import Profile
from app.profiles.service import ProfileResolver

CFG = load_model_config(BACKEND_DIR / "config" / "model.default.yaml")


@pytest.fixture
def resolver() -> ProfileResolver:
    return ProfileResolver(CFG, default_preset="family", max_param_len=2000)


def test_report_example_url_decodes(resolver: ProfileResolver) -> None:
    # Section 3.2: ?p=eyJ2IjoxLCJwcm9maWxlIjoicGVuc2lvbmVyIn0 -> {"v":1,"profile":"pensioner"}
    profile = resolver.resolve(decode_param("eyJ2IjoxLCJwcm9maWxlIjoicGVuc2lvbmVyIn0"))
    assert profile.preset == "pensioner"
    assert profile.filters.mobility == "no_car"


def test_legacy_flat_filters(resolver: ProfileResolver) -> None:
    profile = resolver.resolve({"v": 1, "profile": "family", "mobility": "no_car"})
    assert profile.preset == "family" and profile.filters.mobility == "no_car"


@pytest.mark.parametrize("compress", [True, False])
def test_roundtrip(resolver: ProfileResolver, compress: bool) -> None:
    original = resolver.resolve({"weights": {"S1": 40, "S2": 10, "S3": 10, "S4": 20, "S5": 10, "S6": 10},
                                 "filters": {"mobility": "no_car", "realtor_mode": True}})
    p = resolver.encode(original, compress=compress)
    assert len(p) < 2000 and "=" not in p and "+" not in p and "/" not in p
    again = resolver.resolve(decode_param(p))
    assert again.hash == original.hash and again.preset is None


def test_accepts_js_deflate_raw_zlib_gzip_and_raw_json() -> None:
    body = {"v": 1, "preset": "realtor"}
    raw = json.dumps(body).encode()
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    deflate_raw = co.compress(raw) + co.flush()
    gz = zlib.compressobj(9, zlib.DEFLATED, 31)
    gzipped = gz.compress(raw) + gz.flush()
    for blob in (deflate_raw, zlib.compress(raw), gzipped):
        assert decode_param(base64.urlsafe_b64encode(blob).decode().rstrip("=")) == body
    assert decode_param(json.dumps(body)) == body
    assert decode_param(encode_param(body)) == body


def test_errors(resolver: ProfileResolver) -> None:
    with pytest.raises(ValidationFailed) as e:
        resolver.resolve({"v": 2})
    assert e.value.code == "profile_version_unsupported"
    with pytest.raises(ValidationFailed) as e:
        resolver.resolve({"preset": "astronaut"})
    assert e.value.code == "profile_unknown_preset"
    with pytest.raises(ValidationFailed) as e:
        resolver.resolve({"weights": {"S1": 90, "S2": 30, "S3": 0, "S4": 0, "S5": 0, "S6": 0}})
    assert e.value.code == "profile_invalid"
    with pytest.raises(ValidationFailed) as e:
        resolver.resolve({"filters": {"teleport": True}})
    assert e.value.code == "profile_invalid"
    with pytest.raises(ValidationFailed) as e:
        decode_param("x" * 2001)
    assert e.value.code == "profile_param_too_long"
    with pytest.raises(ValidationFailed) as e:
        decode_param("!!!not-base64!!!")
    assert e.value.code == "profile_param_invalid"


def test_partial_profile_merges_over_preset(resolver: ProfileResolver) -> None:
    p = resolver.resolve({"preset": "pensioner", "filters": {"climate_sensitivity": "low"}})
    assert p.preset == "pensioner"
    assert p.filters.mobility == "no_car"  # from the preset
    assert p.filters.climate_sensitivity == "low"  # override
    assert resolver.pristine_preset_id(p) is None  # modified preset is not the preset
    assert resolver.pristine_preset_id(resolver.preset("pensioner")) == "pensioner"


def test_hash_ignores_label_and_number_format() -> None:
    w = {"S1": 25, "S2": 30, "S3": 15, "S4": 15, "S5": 10, "S6": 5}
    a = Profile(preset="family", weights=w)
    b = Profile(preset=None, weights={k: float(v) for k, v in w.items()})
    assert a.hash == b.hash
    assert a.mode == "car" and a.allowed_clusters == ("A", "B", "V")


def test_default_and_query_resolution(resolver: ProfileResolver) -> None:
    assert resolver.from_query(None, None).preset == "family"
    assert resolver.from_query("pensioner", None).preset == "pensioner"
    p = resolver.encode(resolver.preset("realtor"))
    assert resolver.from_query("pensioner", p).preset == "realtor"  # p wins (URL is the source of truth)
