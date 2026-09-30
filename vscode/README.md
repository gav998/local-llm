# Portable VS Code agent kit

Эта папка не является workspace. Это переносимый профиль VS Code с глобальными
HTTP MCP-подключениями, инструкциями и custom agents.

## Установка

1. Скопируйте всю папку `vscode` рядом с `Code.exe`. Должно получиться:

   ```text
   VSCode\Code.exe
   VSCode\vscode\start.bat
   ```

2. Всегда запускайте редактор через `vscode\start.bat`. Скрипт задаёт отдельные
   portable `user-data`, `extensions` и домашний каталог агента внутри `vscode`.
   Системные `%APPDATA%`, `%LOCALAPPDATA%`, реестр и `PATH` не изменяются.
3. Установите нужное расширение агента в этом окне VS Code. Расширения сохраняются
   в `vscode\extensions`.
4. Отдельно запустите `gost-gsm-mcp\start.bat` и/или
   `office-tools\start.bat`. Серверы не являются частью VS Code и продолжают быть
   независимыми папками.
5. В VS Code выполните `MCP: List Servers`. Должны быть видны `gostGsm` и
   `office`. Затем выберите `Local Document Worker` или `GSM GOST Orchestrator`.

Можно открывать любую папку с документами: агенты и MCP зарегистрированы на уровне
portable-профиля, а не workspace. Если VS Code уже был запущен другим способом,
полностью закройте его перед первым запуском через `vscode\start.bat`.

## Адреса

- GOST GSM MCP: `http://127.0.0.1:8765/mcp`, health:
  `http://127.0.0.1:8765/health`;
- Office MCP: `http://127.0.0.1:8766/mcp`, health:
  `http://127.0.0.1:8766/health`.

Одинаковые подключения записаны в двух форматах: VS Code Local читает
`user-data\User\mcp.json`, а Agent Host —
`profile\.copilot\mcp-config.json`. Custom agents и общие инструкции находятся в
`profile\.copilot`, то есть не зависят от открытого проекта.

Если порты меняются в `server.json` соответствующего сервера, обновите оба MCP
config-файла в этой папке.

Конфигурацию локальной LLM после запуска `llama\start.bat` скопируйте из
`llama\vscode-model.json` в
`vscode\user-data\User\chatLanguageModels.json`.
