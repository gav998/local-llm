# Stack orchestrator

This directory owns no vendor payload and no process. The root
`LOCAL-LLM.bat` calls the public `MODULE.bat` contract of each extracted
module in dependency order. A module can always be installed, started,
stopped and verified without this orchestrator.

`PREPARE-STACK.bat` creates this orchestrator archive in the root `prepared`
directory after confirming that all nine module archives are present there.
The ZIP includes the pinned 7-Zip extractor and `EXTRACT-MODULES.bat`. Put the
ZIP and all module archives in a separate deployment directory, extract only
the ZIP, run `EXTRACT-MODULES.bat`, and then run `LOCAL-LLM.bat install`.
