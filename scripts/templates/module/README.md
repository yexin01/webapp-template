# Module templates

`pnpm new:module <name>` renders these files. Tokens:

| Token | Example (`time_entries`) |
|---|---|
| `__module__` | `time_entries`: Python package, table, permission and event prefix |
| `__path__` | `time-entries`: URL segment and web feature folder |
| `__Module__` | `TimeEntries`: class and component prefix |
| `__entity__` | `time_entry` |
| `__Entity__` | `TimeEntry`: entity and schema name |
| `__label__` | `time entries`: human-readable plural |

The templates follow the projects module, which is the reference
implementation. When you change a pattern there, change it here too;
`pnpm test:new-module` generates a sample module and runs the API and web
checks on it.
