# MySQL module

Independent MySQL 8.0.40 package. It binds to `127.0.0.1:3306`, creates the
`rag_flow` database and a random least-scope `ragflow` account, then publishes
its private connection contract in `state/connection.json`.

Run `PREPARE-ONLINE.bat` on an online Windows machine. Extract the resulting
archive into the stack root, then run `MODULE.bat install`, `start`, `verify`.
