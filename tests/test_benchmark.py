from __future__ import annotations

from allram import benchmark, presets


def test_bench_memory_reports_numbers():
    write, read, latency = benchmark.bench_memory(8)
    assert write > 0
    assert read > 0
    assert 0 < latency < 10_000_000


def test_bench_disk_roundtrip(tmp_path):
    write, read, path = benchmark.bench_disk(str(tmp_path), 4)
    assert write > 0
    assert read > 0
    assert path == str(tmp_path)
    assert not list(tmp_path.glob("allram-bench-*"))


def test_benchmark_dict_shape():
    result = benchmark.run(ram_mib=4, disk_mib=2)
    data = result.as_dict()
    for key in ("ram_write_mbps", "ram_read_mbps", "disk_write_mbps", "disk_read_mbps", "slowdown"):
        assert key in data


def test_estimate_model_spills_to_disk():
    bench = benchmark.BenchmarkResult(ram_write_mbps=20000, disk_write_mbps=500, disk_read_mbps=2000)
    estimate = benchmark.estimate_model(40 * 1024**3, bench, ram_bytes=16 * 1024**3, commit_bytes=80 * 1024**3)
    assert estimate.spill_bytes > 0
    assert estimate.hybrid_load_seconds > estimate.ram_load_seconds
    assert not estimate.fits_in_ram


def test_estimate_model_fits():
    bench = benchmark.BenchmarkResult(ram_write_mbps=20000, disk_write_mbps=500)
    estimate = benchmark.estimate_model(4 * 1024**3, bench, ram_bytes=16 * 1024**3, commit_bytes=80 * 1024**3)
    assert estimate.spill_bytes == 0
    assert estimate.fits_in_ram


def test_estimate_model_not_enough():
    bench = benchmark.BenchmarkResult(ram_write_mbps=20000, disk_write_mbps=500)
    estimate = benchmark.estimate_model(100 * 1024**3, bench, ram_bytes=8 * 1024**3, commit_bytes=16 * 1024**3)
    assert any("не удастся" in note for note in estimate.notes)


def test_model_size_known_and_params():
    assert presets.model_size("llama3.1:8b") == presets.KNOWN_MODELS["llama3.1:8b"]
    size = presets.model_size("13b")
    assert size is not None and size > 13 * 0.5 * 1024**3
    assert presets.model_size("не-модель") is None


def test_presets_targets():
    preset = presets.get("ai")
    assert preset is not None
    size = preset.target_bytes(1000 * 1024**3)
    assert size > 0 and size % (1024**2) == 0
    assert presets.get("nope") is None
