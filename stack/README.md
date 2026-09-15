# Stack orchestrator

This directory owns no vendor payload and no process. The root
`LOCAL-LLM.bat` calls the public `MODULE.bat` contract of each extracted
module in dependency order. A module can always be installed, started,
stopped and verified without this orchestrator.

`PREPARE-STACK.bat` creates only this orchestrator archive after confirming
that all eight module archives have been prepared. Extract the orchestrator
ZIP and all module `.7z` archives into a separate deployment directory before
running `LOCAL-LLM.bat install`.
