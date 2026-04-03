# Navien для Home Assistant

Кастомная интеграция для Home Assistant с поддержкой настройки через UI.

## Возможности
- настройка через `Config Flow`
- сущности:
  - `water_heater`
  - `sensor`
  - `switch`
  - `number`
- облачное подключение NaviLink
- готово для установки через HACS

## Установка через HACS
1. Открой **HACS → Интеграции → Пользовательские репозитории**.
2. Добавь репозиторий:
   ```text
   https://github.com/bezuglyy/navien
   ```
3. Выбери тип **Integration**.
4. Установи интеграцию и перезапусти Home Assistant.

## Ручная установка
Скопируй папку:
```text
custom_components/navien
```
в:
```text
/config/custom_components/navien
```

## Структура
```text
custom_components/navien/
```

## Совместимость
- Home Assistant: 2025.10.0+
- Версия интеграции: 1.5.2

## Репозиторий
- Документация: https://github.com/bezuglyy/navien
- Issues: https://github.com/bezuglyy/navien/issues
