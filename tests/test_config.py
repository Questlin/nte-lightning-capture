from nte_lightning.config import AppConfig, Region, load_config, save_config


def test_round_trip(tmp_path):
    cfg = AppConfig(regions=[Region(10, 20, 300, 200)], regions_ref_size=(3840, 2160), shutter_key="Mouse Left")
    cfg.detection.trigger_pct = 12.5
    path = tmp_path / "config.json"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded == cfg


def test_broken_or_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.json") == AppConfig()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_config(bad) == AppConfig()


def test_values_are_clamped():
    cfg = AppConfig.from_dict({"max_photos": -5, "detection": {"jump": 999}})
    assert cfg.max_photos == 0
    assert cfg.detection.jump == 255


def test_regions_follow_resolution_changes():
    cfg = AppConfig(regions=[Region(1920, 0, 960, 540)], regions_ref_size=(3840, 2160))
    assert cfg.regions_for_size(1920, 1080) == [Region(960, 0, 480, 270)]
    # Regions falling off-screen are dropped, partial ones clipped.
    cfg = AppConfig(regions=[Region(100, 100, 50, 50), Region(5000, 0, 10, 10)], regions_ref_size=(0, 0))
    assert cfg.regions_for_size(120, 120) == [Region(100, 100, 20, 20)]


def test_hold_range_and_trigger_mode_are_clamped():
    cfg = AppConfig.from_dict({"key_hold_min_ms": 80, "key_hold_max_ms": 20, "trigger_mode": "some"})
    assert (cfg.key_hold_min_ms, cfg.key_hold_max_ms) == (80, 80)
    assert cfg.trigger_mode == "any"


def test_frames_are_saved_only_in_debug_mode():
    assert not AppConfig(save_frames=True).saving_frames
    assert AppConfig(save_frames=True, debug=True).saving_frames
