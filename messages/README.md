# Messages

What sūveryn says when something is refused, down, broken or to be checked is a code of one list:
suveryn-core's registry of messages (`messages.json`) and the module that is the one way to use it
(`suveryn_harness/messages.py`, standard library only). The handbook's decision *Messages are codes*;
suveryn-core's [`docs/design/messages.md`](https://github.com/suveryn/suveryn-core/blob/main/docs/design/messages.md).

The appliance's services say those codes and keep no list of their own (suveryn-tracker#141, #154):

| Service | Says | Where the code goes |
|---|---|---|
| The web setup ([`setup`](../setup/README.md)) | the `setup` area, and the `backup` area for a share's test and a restore | Its own API's answers, as Problem Details |
| The backup service ([`backup`](../backup/README.md)) | the `backup` area | `failure` in `status.json`, `key-test.json` and `request-result.json`; `fields` for the settings form |
| The clock's helper ([`clock`](../clock/README.md)) | the `clock` area | `failure` in `result.json` |
| The console: the installer and the installed appliance's screen ([`image/mkosi`](../image/mkosi/README.md), *What can't be done, as a code*) | the `network` and `bundle` areas | The engine's `Refused.code` and `Invalid.code`; `{"failure": …}` from a failed installation. Worded in `installer-text.json`, under the code |

| File | What |
|---|---|
| [`suveryn-messages`](suveryn-messages) | Loads core's module from the core on this appliance, the pinned one at `/opt/suveryn/core` (`SUVERYN_CORE_MESSAGES` for tests). `said(code, …)` gives `{kind, code, params}` as the registry has it; `reference()` a short id for a failure nobody planned for |

## What a service does

- **It names the code where the failure happens** (`raise Failed("backup.disk_full", "…")`): no table from
  its own words to codes. The sentence beside the code is for the journal, never for a screen.
- **A failure nobody planned for** is `system.internal` with a reference: the reference is in the message
  and in the journal's line with the detail, so the administrator's screen and the log meet.
- **The gateway passes a helper's message on** and words nothing: it reads it against its own registry
  (core's `messages.stored`), so a code it doesn't list reaches a screen as "something went wrong", never
  as a raw word.
- **A missing core doesn't stop a backup.** The web setup doesn't start without core's module. The backup
  service and the clock's helper still do their work and write the code as it is.
- **A code is added, renamed or removed in core first**, and the change reaches the appliance when the pin
  moves, in the same PR as the service that says the code (the handbook's [*How a change reaches
  `main`*](https://github.com/suveryn/suveryn-handbook/blob/main/context/development-context.md#how-a-change-reaches-main), item 5).

## Tests

```bash
SUVERYN_CORE=../suveryn-core python3 -m pytest messages/tests
```

Every code a service or the console names is one core's registry lists; no service answers with a word of its
own; a helper still says its code where core can't be read; the console's text file has words in every
language for every code it can say, and shows no sentence of its engine.
