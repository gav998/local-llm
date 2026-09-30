# Portable VS Code agent kit

Каталог использует штатный Portable Mode из Windows ZIP-версии VS Code. Папка
`data` рядом с `Code.exe` заставляет редактор хранить сессии, настройки,
расширения и прочие данные внутри этого каталога. Никакие BAT/PowerShell launcher'ы
для VS Code не нужны.

## Установка

1. Скачайте на официальной странице VS Code архив **Windows x64 ZIP**. User/System
   Installer не подходит для Portable Mode.
2. Распакуйте содержимое архива прямо в эту папку `vscode`, не удаляя уже
   существующую папку `data`. Итоговая структура должна выглядеть так:

   ```text
   local_llm\
     vscode\
       Code.exe
       bin\
       data\
         extensions\                  # появится при установке расширений
         tmp\
         user-data\
           User\
             mcp.json
             settings.json
             prompts\
               *.agent.md
               *.instructions.md
   ```

3. Запускайте `vscode\Code.exe` напрямую — из Проводника или обычным ярлыком на
   этот exe. VS Code сам обнаруживает соседнюю папку `data`.
4. Установите нужное расширение агента в этом VS Code. Расширения сохранятся в
   `vscode\data\extensions`, пользовательские данные — в
   `vscode\data\user-data`, а временные файлы — в `vscode\data\tmp`.
5. Отдельно запустите `gost-gsm-mcp\start.bat` и/или
   `office-tools\start.bat`. Затем в VS Code выберите Session Target `Local` и
   выполните `MCP: List Servers`: должны быть видны `gostGsm` и `office`.

Можно открывать любую папку с документами: агенты, инструкции и MCP записаны в
user-профиле portable VS Code, а не в конкретной workspace.

## Что уже настроено

- `data\user-data\User\mcp.json` — HTTP MCP-подключения;
- `data\user-data\User\settings.json` — настройки Local Agent;
- `data\user-data\User\prompts\*.agent.md` — custom agents;
- `data\user-data\User\prompts\*.instructions.md` — общие инструкции Local
  Agent;
- `data\tmp` — локальный TMP редактора.

Адреса сервисов:

- GOST GSM MCP: `http://127.0.0.1:8765/mcp`, health:
  `http://127.0.0.1:8765/health`;
- Office MCP: `http://127.0.0.1:8766/mcp`, health:
  `http://127.0.0.1:8766/health`.

Если порты меняются в `server.json` соответствующего сервера, обновите
`data\user-data\User\mcp.json`.

Конфигурацию локальной LLM после запуска `llama\start.bat` скопируйте из
`llama\vscode-model.json` в
`vscode\data\user-data\User\chatLanguageModels.json`.

## Обновление VS Code

Распакуйте файлы новой Windows ZIP-версии поверх каталога `vscode`, сохранив
папку `data`. Именно перенос `data` между ZIP-версиями рекомендует официальная
документация VS Code.

Официальная инструкция: [Portable mode](https://code.visualstudio.com/docs/setup/portable).
