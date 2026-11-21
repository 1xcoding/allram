"""Готовые сценарии («пресеты») и подсказки по нейросетям."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import util


@dataclass(frozen=True)
class Preset:
    key: str
    title: str
    description: str
    ram_share: float  # доля свободного места SSD под «расширение ОЗУ»
    min_bytes: int
    pagefile: bool = True
    ramdisk: bool = False
    notes: tuple[str, ...] = ()

    def target_bytes(self, free_bytes: int) -> int:
        target = int(free_bytes * self.ram_share)
        return util.round_down_to_mib(max(target, self.min_bytes))


PRESETS: dict[str, Preset] = {
    "ai": Preset(
        key="ai",
        title="Нейросети (LLM, Stable Diffusion, whisper)",
        description="Максимум модели — в памяти, кэш и временные файлы — на быстром диске.",
        ram_share=0.66,
        min_bytes=16 * util.GIB,
        pagefile=True,
        ramdisk=False,
        notes=(
            "запускайте GGUF-модели с mmap: llama.cpp сам догружает слои из подкачки",
            "для Ollama задайте OLLAMA_MODELS на быстрый SSD",
        ),
    ),
    "browser": Preset(
        key="browser",
        title="Много вкладок и тяжёлые программы",
        description="Сглаживает нехватку памяти при десятках вкладок и виртуальных машинах.",
        ram_share=0.5,
        min_bytes=8 * util.GIB,
        pagefile=True,
    ),
    "games": Preset(
        key="games",
        title="Игры и моды",
        description="Убирает «вылеты по памяти» в тяжёлых играх, но не даёт FPS.",
        ram_share=0.5,
        min_bytes=12 * util.GIB,
        pagefile=True,
        notes=("файл подкачки не ускоряет игру — он только спасает от нехватки памяти",),
    ),
    "max": Preset(
        key="max",
        title="Максимум свободного SSD",
        description="Почти всё свободное место диска становится расширением ОЗУ.",
        ram_share=0.85,
        min_bytes=32 * util.GIB,
        pagefile=True,
        notes=("оставляйте на системном диске хотя бы 10–15 % свободного места",),
    ),
    "ramdisk": Preset(
        key="ramdisk",
        title="RAM-диск для кэшей (ImDisk)",
        description="Часть физической ОЗУ становится очень быстрым диском.",
        ram_share=0.0,
        min_bytes=1 * util.GIB,
        pagefile=False,
        ramdisk=True,
        notes=("данные на RAM-диске исчезают при выключении — только для кэшей и временных файлов",),
    ),
}


def get(key: str) -> Preset | None:
    return PRESETS.get(str(key).lower())


def list_presets() -> list[Preset]:
    return list(PRESETS.values())


def plan_for_preset(preset: Preset, free_bytes: int) -> dict[str, Any]:
    size = preset.target_bytes(free_bytes)
    return {
        "preset": preset.key,
        "title": preset.title,
        "size_bytes": size,
        "size": util.human_bytes(size),
        "pagefile": preset.pagefile,
        "ramdisk": preset.ramdisk,
        "notes": list(preset.notes),
    }


# ---------------------------------------------------------------------------
# Оценки для нейросетей
# ---------------------------------------------------------------------------

# Грубые ориентиры по популярным моделям (квантование Q4).
KNOWN_MODELS: dict[str, int] = {
    "qwen2.5:0.5b": int(0.4 * util.GIB),
    "qwen2.5:1.5b": int(1.1 * util.GIB),
    "qwen2.5:3b": int(2.1 * util.GIB),
    "llama3.2:1b": int(0.8 * util.GIB),
    "llama3.2:3b": int(2.0 * util.GIB),
    "llama3.1:8b": int(4.9 * util.GIB),
    "mistral:7b": int(4.4 * util.GIB),
    "gemma2:9b": int(5.5 * util.GIB),
    "qwen2.5:14b": int(9.0 * util.GIB),
    "qwen2.5:32b": int(20.0 * util.GIB),
    "llama3.1:70b": int(42.0 * util.GIB),
    "sdxl": int(7.0 * util.GIB),
    "sd15": int(4.0 * util.GIB),
    "flux.1-dev": int(24.0 * util.GIB),
    "whisper-large": int(3.1 * util.GIB),
}


def model_size(name: str) -> int | None:
    """Ищет размер модели по имени/параметрам (например, '13b' → ~8 ГиБ Q4)."""
    text = str(name).strip().lower()
    if text in KNOWN_MODELS:
        return KNOWN_MODELS[text]
    import re

    match = re.search(r"(\d+(?:\.\d+)?)\s*b\b", text)
    if match:
        params = float(match.group(1))
        # Q4_K_M ≈ 0.62 ГиБ на миллиард параметров + оверхед
        return int(params * 0.62 * util.GIB) + int(0.6 * util.GIB)
    return None


def models_table(commit_bytes: int) -> list[tuple[str, int, bool]]:
    rows: list[tuple[str, int, bool]] = []
    for name, size in KNOWN_MODELS.items():
        rows.append((name, size, size <= commit_bytes))
    return rows
