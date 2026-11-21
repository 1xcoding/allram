# История изменений

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — по [Semantic Versioning](https://semver.org/lang/ru/).

## [1.0.0] — 2026-11-21

### Добавлено

- Настройка файла подкачки на выбранном SSD с записью в реестр
  (`PagingFiles`, `AutomaticManagedPagefile`) и резервной копией исходных значений.
- Команда `revert` — откат к предыдущим настройкам подкачки.
- Профили `ai`, `browser`, `games`, `max`, `ramdisk` и автоподбор размера.
- RAM-диск ImDisk: установка драйвера, создание, удаление, перенос TEMP/TMP.
- Наблюдатель `watch` с порогами занятости памяти и JSON-режимом.
- Замер скорости ОЗУ и SSD (`bench`) и оценка загрузки моделей (`models`).
- Чтение физической памяти из `HARDWARE\RESOURCEMAP` без прав администратора.
- Интерактивное меню, цвета, UTF-8 в консоли Windows, коды возврата для скриптов.
- Сборка единого `allram.exe` через PyInstaller + workflow GitHub Actions.
- 59 автотестов (Linux и Windows).
