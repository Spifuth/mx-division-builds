# Division 2 Builds tool

[![License: CC BY-NC-SA 4.0](https://licensebuttons.net/l/by-nc-sa/4.0/80x15.png)](https://creativecommons.org/licenses/by-nc-sa/4.0/)

## Running it

Everything runs in Docker — the host needs Docker and nothing else, no Node
and no npm. There is nothing to configure: the game data ships in
`public/data/`.

```sh
./scripts/dev.sh up -d app     # dev server, tailnet-only
./scripts/dev.sh logs -f app   # the address it published on
```

Details, the one-shot runner for `npm test` / `npm run check` /
`npm run build-prod`, and what each of those does and does not prove:
[`docs/dev.md`](docs/dev.md).

## Credits

A huge "Thank you" to:

-   GyroTwister
-   Kiochy
-   FROST
-   RedKnightAMW
-   TheSoldier
-   BANNED.
-   ["The Division 2 - Gear Attribute Sheet" Team](https://docs.google.com/spreadsheets/d/1REi6cA7oSzT7h0O9YD6uxAbnCTmE-YKMDctsKogzXC8/pubhtml#)
-   saagri (NPCs HP values)
-   Vikeman45 (NPCs HP values)
-   [" Ruben Alamina - THE DIVISION 2: WEEKLY VENDOR RESET"](https://rubenalamina.mx/the-division-weekly-vendor-reset/)
