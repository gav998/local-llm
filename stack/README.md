# Stack orchestrator

This directory owns no vendor payload and no process. The root
`LOCAL-LLM.bat` calls the public `MODULE.bat` contract of each extracted
module in dependency order. A module can always be installed, started,
stopped and verified without this orchestrator.
