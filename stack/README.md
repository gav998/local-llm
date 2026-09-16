# Stack orchestrator

This directory owns no vendor payload and no process. The root
`LOCAL-LLM.bat` calls the public `MODULE.bat` contract of each extracted
module in dependency order. A module can always be installed, started,
stopped and verified without this orchestrator.

`PREPARE-STACK.bat` creates this orchestrator archive after confirming that all
eight module archives have been prepared. The ZIP includes the pinned 7-Zip
extractor, archive hashes and `EXTRACT-MODULES.bat`. Put the ZIP and all module
archives in a separate deployment directory, extract only the ZIP, run
`EXTRACT-MODULES.bat`, and then run `LOCAL-LLM.bat install`.
