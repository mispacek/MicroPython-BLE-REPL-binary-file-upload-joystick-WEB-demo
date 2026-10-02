# MicroPython BLE REPL – knihovna a webové demo

Samostatný driver pro **MicroPython 1.29 / ESP32 / NimBLE** a podrobně komentované
webové demo pod **MIT**. Obsahuje skutečný REPL, Python editor se zvýrazněním,
spouštění a zastavení kódu, binární upload souborů a dva joysticky.

[English README](README.md) · [Popis protokolu](PROTOCOL.md) · [Ověření](TESTING.md)

**[Otevřít online demo](https://mispacek.github.io/MicroPython-BLE-REPL-binary-file-upload-joystick-WEB-demo/)** — běží na GitHub Pages přes HTTPS, místní server není potřeba.

## Rychlé spuštění

1. Přes USB nahraj `firmware/ble_repl.py` do desky jako `/ble_repl.py`.
   `firmware/bletime.py` je volitelný pro staré importy; driver ho nepotřebuje.
2. V USB REPLu spusť:

   ```python
   from ble_repl import start_ble_repl
   ble = start_ble_repl(name="MPY-BLE-DEMO")
   ```

3. Otevři **[online demo](https://mispacek.github.io/MicroPython-BLE-REPL-binary-file-upload-joystick-WEB-demo/)**
   v prohlížeči s Web Bluetooth.
4. Klikni **Connect board** a vyber desku.

Pro místní vývoj spusť z adresáře repozitáře `python -B serve.py` a otevři
**http://localhost:8087/web/**. V online demu komunikuje prohlížeč přímo s deskou
přes BLE; GitHub poskytuje pouze statické HTML, JavaScript a CSS.

BLE jméno má 1–29 UTF-8 bajtů. Driver vyžaduje BLE/NimBLE, `micropython.RingIO`,
native emitter a měkký `machine.Timer`. Fyzicky ověřená deska je ESP32-C3 s
MicroPythonem 1.29. Ostatní modely ESP32 potřebují vlastní kontrolu.
Pro automatický start si můžeš stejný příkaz vědomě přidat do svého `main.py`.
Projekt startup sám neupravuje. Druhý BLE vlastník nebo obsazený dupterm slot
se odmítne; původní vlastník musí být nejprve uzavřený.

Web potřebuje HTTPS nebo localhost; otevření přes `file://` není doporučený postup.
GitHub Pages publikuje větev **`main` / kořen repozitáře**. Úvodní stránka přesměruje
na `/web/`, `.nojekyll` zachovává soubory beze změny. Další push do `main` web
automaticky aktualizuje.
Stránka nevyužívá CDN, externí fonty ani npm knihovny.

## REPL a editor

Po připojení klikni přímo do terminálu a napiš například:

```python
print("Ahoj z ESP32")
import os; print(os.listdir('/'))
from ble_repl import get_active; print(get_active().stats())
```

**Enter** příkaz spustí. Klávesy se posílají přímo do MicroPythonu; ten zajišťuje
echo, historii i doplňování. xterm.js vykresluje kurzor a ANSI sekvence i tehdy,
když jsou rozdělené mezi BLE notifikace. Prohlížeč napsané znaky sám nepřidává.

| Klávesa/ovládání | Funkce |
| --- | --- |
| ↑ / ↓ | Historie příkazů na desce |
| ← / →, Home / End, Backspace / Delete | Úprava rozepsaného příkazu |
| Tab | Doplnění názvu přes MicroPython |
| Ctrl+C / Interrupt | Přerušení Pythonu nebo zrušení běžícího Run/uploadu |
| Ctrl+A / Raw, Ctrl+B / REPL | Raw režim / návrat do friendly REPLu |
| Ctrl+E / Paste, Ctrl+D / End | Začátek paste režimu / spuštění vloženého kódu |
| Ctrl/⌘+V | Vložení textu ze schránky |
| Ctrl/⌘+K / Clear | Vymazání lokální historie výstupu se zachováním aktuálního řádku |
| Shift+Tab | Přesun fokusu mimo terminál |

Víceřádkový kód vkládej takto: **Paste**, vložení ze schránky se správným
odsazením, **End**. Tím se vyhneš automatickému odsazování friendly REPLu.
Ctrl+D ve friendly režimu provádí soft reset a může odpojit ručně spuštěný driver.
Pro větší programy použij editor.

Během uploadu a přípravy REPLu je psaní pozastavené. Jakmile program z editoru
běží, lze v terminálu odpovídat na Python `input()`. Přepínání REPL režimů je
během Run zablokované, Ctrl+C zůstává dostupné.

Editor zvýrazňuje Python, Tab vloží čtyři mezery, **Load .py / Save .py**
otevře/uloží lokální soubor. Zvýraznění je jednoduchý lexer, ne kontrola syntaxe.

**Run** uloží UTF-8 obsah editoru do viditelné cílové cesty, standardně
`/ble_demo_run.py`, a spustí ho přes raw REPL. Soubor na desce zůstává a při dalším
Run se přepíše. Program běží v globálním prostoru REPLu, kde jeho proměnné zůstávají
do resetu. Zdroj se nejprve nahraje jako soubor, takže se nemusí celý vejít do 2048B
REPL bufferu; na jeho kompilaci ovšem stále musí stačit RAM desky.
Klávesová zkratka **Ctrl/⌘+Enter** také spouští. Výstup i Python výjimky jsou
v terminálu. Po dokončení se klient vrátí do friendly REPLu.

**Stop** přeruší Python přes Ctrl+C a ponechá BLE připojené. Během uploadu nejprve
odešle CANCEL a uzavře rozpracovaný soubor. Program může `KeyboardInterrupt` zachytit;
blokující nativní operace nebo vypnuté přerušování mohou zastavení zpozdit či znemožnit.
Ctrl+D ve friendly REPLu je soft reset, nikoli bezpečný ekvivalent Stop.

```python
import time

try:
    while True:
        print("Běžím")
        time.sleep_ms(500)
except KeyboardInterrupt:
    print("Zastaveno; BLE funguje dál")
```

## Upload souborů

Vyber soubor a zadej cílovou cestu, například `/lib/helper.py` nebo `/assets/image.bin`.
**Upload file** přenáší skutečné bajty, takže fungují i `.mpy` a jiné binární soubory.
Neexistující složky vytvoří driver.

**Cíl se zapisuje přímo a existující soubor se přepíše.** Stop, chyba nebo odpojení
mohou zanechat neúplný soubor; stará verze se automaticky neobnoví. Poslední ACK přijde
až po close/sync. Klient při ztraceném ACK zjišťuje STATUS a pokračuje od potvrzeného
pořadového čísla, místo aby soubor znovu otevřel a zkrátil. SUM8 kontroluje pakety;
fyzický test navíc ověřuje SHA-256 přes nezávislé USB čtení.

Limit je méně než 65 536 DATA paketů a nejvýše 16 777 215 B; při MTU 23 je skutečný
limit přísnější. Název má nejvýše **48 UTF-8 bajtů**, nikoli 48 znaků.
Rozpracovaný paket má timeout 1 s, nečinný přenos 10 s.

## Joystick

Driver musí být spuštěný; nevytvářej jeho druhou instanci. Do editoru vlož:

```python
from ble_repl import Joystick, joy_read
import time

left = Joystick(0)
right = Joystick(10)

try:
    while True:
        print("Levý:", left.get_joyX(), left.get_joyY(),
              "Pravý:", right.get_joyX(), right.get_joyY())
        if left.joy_check(1):
            print("Levý joystick míří nahoru")
        time.sleep_ms(250)
except KeyboardInterrupt:
    print("Joystick zastaven")
```

Ovládej myší, dotykem nebo šipkami při zaměření příslušného padu. Nahoru je kladné Y.
Osy mají rozsah −100…100; při uvolnění nebo ztrátě fokusu se vrátí na nulu.
Při držení se aktualizují každých 80 ms; upload je dočasně pozastaví.
Gettery vrací nulu, když jsou data stará alespoň 3000 ms.

| API | Význam |
| --- | --- |
| `Joystick(10)` | Pravý joystick; jiné hodnoty vybírají levý. |
| `get_joyX()`, `get_joyY()` | Omezené osy se stale ochranou. |
| `joy_check(1/2/3/4)` | Nahoru/vpravo/dolů/vlevo za prahem ±40. |
| `joy_check(5)` | Obě osy jsou nenulové; nejde o tlačítko. |
| `joy_read()` | Sdílený seznam `[Lx,Ly,Rx,Ry]` a stáří; raw seznam se při zastarání sám nevynuluje. |

Pokud potřebuješ stabilní snímek, seznam zkopíruj. Čas čti přes `joy_read()` nebo
`ble_repl.JOY_TS_MS`, protože jednorázový import číselné hodnoty nesleduje další změny.

## Diagnostika a životní cyklus

```python
from ble_repl import get_active

ble = get_active()
print(ble.stats())
print(ble.mem_usage())
# ble.close()  # Z USB: záměrně vypne transport a odpojí BLE terminál.
```

`preferred_mtu` je pouze požadavek. Skutečné spojení popisují `negotiated_mtu`,
`mtu_confirmed` a `chunk`. MTU 247 dovoluje payload 244 B a file data 240 B;
MTU 23 používá 20/16 B. JavaScript nemůže větší MTU vynutit.
`notify_errors` zahrnuje úspěšně obnovené chyby. `fault` je poslední historický důvod
poruchy, nikoli příznak aktivní chyby; sleduj současně `connected` a `ready`.
`mem_usage()` uvádí GC celé VM. Obě diagnostické funkce patří do foregroundu.

Konstruktor `BLENUSRepl(name=...)` ještě nespouští reklamu; následuje `.start()`.
`start_ble_repl()` obojí spojí. `.close()` je opakovatelný; po úspěšném uzavření
při restartu vytvoř nový objekt. Zachované aliasy jsou `IDEBLERepl`,
`start_ble_repl_bletime` a po startu také import `ble_repl_bletime` pro starý joystick.
Driver nepřepisuje `utime` a nevkládá globální funkce aplikace, například `clear_stop()`.

Mechanika používá pevné RX/TX 2048 B, ingress 4096 B, měkký 20ms timer, odložené
file I/O a prioritní odpovědi protokolu. Při přetížení stdout se nové výstupy
zahazují a počítají; při přetečení vstupu se spojení odpojí, aby se nespustil
zkrácený Python příkaz.

## Vlastní JavaScript projekt

Importuj `BleReplClient` z `web/ble-client.js`; `connect()` volej po kliknutí:

```javascript
import { BleReplClient } from './web/ble-client.js';
import { createReplTerminal } from './web/terminal.js';
const ble = new BleReplClient();
const report = error => { if (error.name !== 'AbortError') console.error(error); };
// HTML musí obsahovat #terminal a lokální xterm/style CSS jako anglický příklad.
const terminal = createReplTerminal(document.querySelector('#terminal'),
  text => ble.writeTerminal(text), report);
ble.on('text', terminal.write);
ble.on('stderr', terminal.write);
const updateInput = () => terminal.setWritable(ble.terminalReady);
ble.on('state', updateInput);
ble.on('terminalReady', updateInput);
// V click handleru: await ble.connect(); await ble.resumeRepl(); terminal.focus();
// Po připojení, se zachycením Promise chyb:
await ble.command('print(42)');
// Pro přímé psaní neposílej command() před každou klávesou:
await ble.writeTerminal('pri');
await ble.writeTerminal('\t');
await ble.writeTerminal('\x03');
await ble.upload('/data.bin', new Uint8Array([0, 255, 128]));
await ble.run('print("Ahoj")');
await ble.joystick([-50, 100, 0, 0]);
await ble.stop();
```

Kompletní HTML integrační příklad je v [anglickém README](README.md#6-integrate-the-javascript-client).
`run()` čeká na dokončení programu bez časového limitu. Stop odmítne aktivní upload/run
s `AbortError`; ostatní chyby zobraz. Události jsou `state`, `device`, `config`,
`text`, `stderr`, `terminalReady`, `notice`. `on()` vrací funkci pro odhlášení.
Interaktivní terminál zapoj přes `createReplTerminal()` z `web/terminal.js`,
jeho vstup směruj do `ble.writeTerminal()` a výstup do `terminal.write()`.
Podle `ble.terminalReady` povoluj vstup. Fronta má limit 4096 UTF-8 bajtů,
zápisy jsou postupné a rozpracované klávesy se před přenosem souboru zahodí.
Opakované joystick
zápisy slučuj a omezuj jako v komentovaném `web/app.js`.

## Licence, bezpečnost a ověření

Balíček včetně této kopie driveru je **MIT**; při distribuci zachovej `LICENSE`.
Držitel práv výslovně schválil vydání nového driveru pod MIT. Původní ESP IDE
a legacy driver nejsou součástí tohoto repozitáře ani zde nejsou přelicencované.

Inspirací pro REPL stránku je [web-bluetooth-repl](https://github.com/siliconwitchery/web-bluetooth-repl)
od Silicon Witchery (ISC). Jeho kód ani grafika se zde nekopírují. Demo je nová
implementace pro tento file/joystick protokol. Terminál používá lokální MIT kopie
xterm.js 6.0.0 a FitAddon 0.11.0; nevyžaduje CDN ani instalaci npm. Při distribuci
zachovej také jejich [licence a copyrighty](web/vendor/README.md).

Driver neposkytuje pairing/bonding ani autentizaci. Zpřístupňuje REPL a zápis
souborů blízkým BLE klientům; pro skutečný produkt musí být přístup řešen podle
jeho požadavků. Oprávnění webového prohlížeče samo neautentizuje jiné BLE klienty.

`npm test` spustí lokální regresní testy s Node 18+ bez instalace balíčků.
[TESTING.md](TESTING.md) odděluje statické, browser a fyzické výsledky.
Zdrojové příklady jsou v `examples/`; specifikace protokolu v `PROTOCOL.md`.
