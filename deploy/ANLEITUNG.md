# Ausführliche Anleitung: regime_trader auf einem Server betreiben

Diese Anleitung führt dich **Schritt für Schritt** durch die Einrichtung des
Trading-Bots auf einem kostenlosen Oracle-Cloud-Server. Sie erklärt bei jedem
Schritt **was** du tust, **warum**, und **was du sehen solltest**. Du musst
kein Linux-Profi sein — jeden Befehl kannst du einfach kopieren und einfügen.

> **Was am Ende herauskommt:** Ein Server, der werktags morgens um 09:35 New
> Yorker Zeit (kurz nach US-Börseneröffnung) automatisch den Bot startet, eine
> Handelsentscheidung für SPY und QQQ trifft, ggf. eine Order platziert, und
> sich wieder schlafen legt. Es bleibt **Spielgeld** (Paper-Trading), bis du
> das später bewusst umstellst.

**Zeitaufwand:** ca. 20–40 Minuten beim ersten Mal.

---

## Inhaltsverzeichnis

1. [Was du vorher brauchst](#0-was-du-vorher-brauchst)
2. [Alpaca-Zugangsdaten holen](#1-alpaca-zugangsdaten-holen)
3. [Den Oracle-Cloud-Server erstellen](#2-den-oracle-cloud-server-erstellen)
4. [Dich mit dem Server verbinden (SSH)](#3-dich-mit-dem-server-verbinden-ssh)
5. [Den Bot-Code auf den Server holen](#4-den-bot-code-auf-den-server-holen)
6. [Die automatische Einrichtung ausführen](#5-die-automatische-einrichtung-ausführen)
7. [Deine Zugangsdaten hinterlegen](#6-deine-zugangsdaten-hinterlegen)
8. [Den Zeitplan starten und testen](#7-den-zeitplan-starten-und-testen)
9. [Prüfen, dass alles läuft](#8-prüfen-dass-alles-läuft)
10. [Der Alltag: was du wissen musst](#9-der-alltag-was-du-wissen-musst)
11. [Wenn etwas nicht klappt (Fehlersuche)](#10-wenn-etwas-nicht-klappt-fehlersuche)
12. [Später: mit echtem Geld handeln](#11-später-mit-echtem-geld-handeln)

---

## 0. Was du vorher brauchst

Drei Dinge, jeweils kostenlos:

- **Ein Alpaca-Konto** (der Broker, über den der Bot handelt) — Registrierung
  auf <https://alpaca.markets>. Das Paper-Trading-Konto (Spielgeld) ist
  automatisch dabei.
- **Ein Oracle-Cloud-Konto** — Registrierung auf
  <https://www.oracle.com/cloud/free/>. Du brauchst eine Kreditkarte zur
  Verifizierung, wirst aber für die "Always Free"-Ressourcen **nicht**
  belastet.
- **Ein Terminal auf deinem eigenen Rechner**, um dich mit dem Server zu
  verbinden:
  - **Mac / Linux:** das Programm "Terminal" ist schon installiert.
  - **Windows:** "PowerShell" (in Windows 10/11 eingebaut) oder das
    "Windows Terminal" aus dem Microsoft Store.

> **Was ist überhaupt ein "Server"?** Ein Computer, der irgendwo im
> Rechenzentrum steht und rund um die Uhr läuft — im Gegensatz zu deinem
> Laptop, den du zumachst. Dein Bot muss zur Börsenöffnung erreichbar sein,
> deshalb braucht er so einen dauerhaft laufenden Rechner.

---

## 1. Alpaca-Zugangsdaten holen

Der Bot muss sich bei Alpaca ausweisen. Dazu brauchst du zwei Schlüssel.

1. Melde dich auf <https://app.alpaca.markets> an.
2. Stelle oben links sicher, dass du im **"Paper"**-Modus bist (nicht "Live").
   Es gibt meist einen Umschalter "Paper / Live".
3. Suche im Menü rechts nach **"API Keys"** (oder "Home" → Abschnitt "Your API
   Keys") und klicke **"Generate New Keys"** / "Neue Schlüssel erzeugen".
4. Es erscheinen **zwei** Werte:
   - **API Key ID** (kürzer, z.B. `PK...`)
   - **Secret Key** (länger)

> **Wichtig:** Der **Secret Key wird nur ein einziges Mal angezeigt.** Kopiere
> beide Werte sofort in eine Textdatei oder einen Passwortmanager. Wenn du ihn
> verlierst, musst du neue Schlüssel erzeugen — das ist kein Drama, aber
> lästig.

Behandle diese Schlüssel wie ein Passwort — teile sie mit niemandem.

---

## 2. Den Oracle-Cloud-Server erstellen

Hier erstellst du den kostenlosen Rechner, auf dem der Bot wohnt.

1. Melde dich auf <https://cloud.oracle.com> an.
2. Klicke oben links auf das **Menü (☰)** → **Compute** → **Instances**.
3. Klicke **"Create Instance"** / "Instanz erstellen".
4. Jetzt die Einstellungen — nur zwei sind wichtig:

   **a) Name:** Egal, z.B. `regime-trader`.

   **b) Image und Shape** (Betriebssystem und Hardware): Klicke bei "Image and
   shape" auf **"Edit"**.
   - **Image:** Wähle **Canonical Ubuntu** (Version 22.04 oder 24.04).
   - **Shape:** Klicke "Change shape" → Reiter **"Ampere"** → wähle
     **VM.Standard.A1.Flex**. Stelle **1 OCPU** und **6 GB RAM** ein.
     > Das ist die "Always Free"-Hardware — dauerhaft kostenlos. Der Bot
     > braucht viel weniger, aber das ist das komfortabelste kostenlose Paket.
     >
     > **Falls "Out of capacity for shape VM.Standard.A1.Flex" kommt:** Die
     > kostenlosen ARM-Server sind in deiner Region gerade ausgebucht (sehr
     > häufig bei Oracle). Nimm stattdessen einfach den anderen kostenlosen
     > Shape: "Change shape" → Reiter **"AMD"** → **VM.Standard.E2.1.Micro**
     > ("Always Free-eligible"). Der hat nur 1 GB RAM statt 6, ist aber für
     > diesen Bot (läuft ~15 s/Tag) mehr als genug, und er ist fast immer
     > verfügbar. Das Setup läuft darauf identisch. Alternativ: andere
     > "Availability Domain" (AD-2/AD-3) probieren oder später nochmal.

   **c) Networking / Netzwerk:** Bei einem **neuen Konto** gibt es noch kein
   Netzwerk, deshalb zeigt Oracle hier oft **"2 Errors"**. Das ist normal —
   lass Oracle das Netzwerk einfach neu anlegen:
   - Bei **"Primary network"**: wähle **"Create new virtual cloud network"**
     (statt "Select existing"). Namen kannst du so lassen.
   - Bei **"Subnet"**: wähle **"Create new public subnet"** (statt "Select
     existing"). Namen so lassen.
   - Bei **"Public IPv4 address assignment"**: schalte
     **"Automatically assign public IPv4 address" auf AN**. Diese öffentliche
     Adresse brauchst du, um dich per SSH zu verbinden.
   - `IPv6` und "Advanced options" ignorieren.
   > Danach sind die Fehler weg. Ein "virtual cloud network" ist das private
   > Netz deines Servers; die öffentliche IP ist die Adresse, unter der du ihn
   > erreichst. Sicher bleibt es trotzdem — die Firewall lässt standardmäßig
   > nur SSH herein, und dein Bot ruft ohnehin nur nach außen.

   **d) SSH-Schlüssel** (dein digitaler Türschlüssel zum Server): Im Abschnitt
   "Add SSH keys" ist meist **"Generate a key pair for me"** vorausgewählt.
   - Klicke **"Save private key"** und **"Save public key"** und speichere
     beide Dateien an einem Ort, den du wiederfindest (z.B. Ordner
     `oracle-key` auf dem Desktop). Die **private** Datei (`...private.key`)
     ist dein Schlüssel — gib sie niemandem.

5. Klicke unten **"Create"**. Nach 1–2 Minuten ist der Server bereit.
6. Auf der Instanz-Seite steht die **"Public IP address"** — eine Zahlenfolge
   wie `130.61.xx.xx`. **Notiere sie dir**, du brauchst sie gleich.

> **Muss ich Ports/Firewall öffnen?** Nein. Der Bot ruft nur nach *außen* (zu
> Alpaca). Es muss niemand von außen auf ihn zugreifen. Die Standard-
> einstellung (nur SSH rein, alles raus) passt.

---

## 3. Dich mit dem Server verbinden (SSH)

"SSH" ist die sichere Fernverbindung zu deinem Server — als würdest du eine
Kommandozeile *auf dem Server* öffnen.

1. Öffne auf deinem eigenen Rechner das Terminal (siehe Schritt 0).
2. Gib diesen Befehl ein — ersetze `PFAD/ZUM/private.key` durch den echten
   Pfad zu deiner gespeicherten privaten Schlüsseldatei, und `DEINE_IP` durch
   die Public IP von eben:

   ```bash
   ssh -i PFAD/ZUM/private.key ubuntu@DEINE_IP
   ```

   Beispiel (Mac, Schlüssel liegt auf dem Desktop):
   ```bash
   ssh -i ~/Desktop/oracle-key/ssh-key.private ubuntu@130.61.12.34
   ```

3. Beim allerersten Mal fragt er *"Are you sure you want to continue
   connecting?"* — tippe **`yes`** und Enter.

4. **Wenn eine Fehlermeldung zu "permissions" / "unprotected key" kommt**
   (häufig auf Mac/Linux), musst du die Schlüsseldatei einmalig absichern:
   ```bash
   chmod 600 PFAD/ZUM/private.key
   ```
   Danach den `ssh`-Befehl erneut ausführen.

**Das solltest du sehen:** Eine Zeile, die etwa so endet:
`ubuntu@regime-trader:~$` — das ist die Kommandozeile *auf dem Server*. Ab
jetzt tippst du Befehle für den Server.

> **Der Benutzer `ubuntu`** ist dein persönlicher Zugang. `sudo` vor einem
> Befehl bedeutet "als Administrator ausführen" — das brauchen die
> Einrichtungsschritte.

---

## 4. Den Bot-Code auf den Server holen

Jetzt holst du dein Programm auf den Server. Führe **auf dem Server** aus:

```bash
sudo apt-get update -qq && sudo apt-get install -y git
git clone DEINE_REPO_URL regime_trader
cd regime_trader
```

- Ersetze `DEINE_REPO_URL` durch die Adresse deines Git-Repositorys (z.B.
  `https://github.com/deinname/regime_trader.git`).
- Der letzte Befehl (`cd regime_trader`) wechselt *in* den heruntergeladenen
  Ordner. Alle folgenden Befehle laufen von hier.

**Das solltest du sehen:** `git clone` zeigt einen Fortschritt und endet mit
etwas wie `Resolving deltas: 100% ... done.`

> **Falls dein Repo privat ist**, fragt Git nach Benutzername/Passwort. Bei
> GitHub brauchst du dann ein "Personal Access Token" statt des Passworts —
> falls das bei dir so ist, sag Bescheid, dann erkläre ich diesen Teil
> separat.

---

## 5. Die automatische Einrichtung ausführen

Ein einziges Skript erledigt die ganze technische Einrichtung:

```bash
sudo bash deploy/setup.sh
```

**Was dieses Skript für dich macht** (du musst nichts davon selbst tun):

- installiert Python und die nötigen System-Pakete,
- legt einen eigenen, abgesicherten Benutzer `regime` an (der Bot läuft nicht
  als du selbst — das ist sicherer),
- kopiert den Code nach `/opt/regime_trader` (der feste Wohnort des Bots),
- richtet eine isolierte Python-Umgebung ein und installiert die Bibliotheken,
- installiert den **Zeitplan** (den systemd-"Timer"), der den Bot täglich
  startet.

Das dauert 1–3 Minuten. **Das solltest du am Ende sehen:** einen Block
"Next steps:" mit den nächsten Befehlen. Wenn dort steht, dass noch keine
`.env` vorhanden ist — das ist normal, das kommt jetzt.

> **Zur Info: was ist ein "Timer"?** Wie eine Zeitschaltuhr. Statt dass der Bot
> pausenlos läuft, schaltet der Timer ihn werktags um 09:35 NY-Zeit für ein
> paar Sekunden ein. Das spart Ressourcen und passt zu einem System, das nur
> einmal am Tag handelt.

---

## 6. Deine Zugangsdaten hinterlegen

Jetzt trägst du die Alpaca-Schlüssel aus Schritt 1 ein. Kopiere diesen Block,
**ersetze aber die beiden Platzhalter** durch deine echten Schlüssel, und füge
ihn dann im Server-Terminal ein:

```bash
sudo -u regime tee /opt/regime_trader/.env >/dev/null <<'EOF'
ALPACA_API_KEY=HIER_DEIN_API_KEY
ALPACA_SECRET_KEY=HIER_DEIN_SECRET_KEY
PAPER=true
EOF
sudo chmod 600 /opt/regime_trader/.env
```

**Erklärung:**
- Die Datei `.env` speichert deine Geheimnisse. Sie liegt nur auf dem Server.
- `PAPER=true` sorgt dafür, dass **garantiert mit Spielgeld** gehandelt wird.
- `chmod 600` macht die Datei so, dass **nur** der Bot sie lesen kann.

> **Kontrolle:** Mit `sudo cat /opt/regime_trader/.env` kannst du prüfen, dass
> deine Schlüssel korrekt drinstehen (keine Tippfehler, keine Leerzeichen).

---

## 7. Den Zeitplan starten und testen

Jetzt aktivierst du den täglichen Zeitplan:

```bash
sudo systemctl start regime-trader.timer
```

Prüfe, wann der Bot das nächste Mal automatisch läuft:

```bash
systemctl list-timers regime-trader.timer
```

**Das solltest du sehen:** eine Tabelle mit einer Spalte "NEXT", die den
nächsten Werktag, 09:35, zeigt.

**Jetzt der wichtige Testlauf** — starte den Bot einmal von Hand, um zu sehen,
dass alles funktioniert, ohne auf morgen früh zu warten:

```bash
sudo systemctl start regime-trader.service
journalctl -u regime-trader.service -f
```

Der zweite Befehl zeigt dir das Live-Protokoll. **Das solltest du sehen:** die
10 Startschritte, dann `[startup 10/10] Startup complete.` und eine
`STATUS | ...`-Zeile, dann `SHUTDOWN REPORT`. Drücke danach **`Strg+C`**, um
die Protokoll-Anzeige zu verlassen (der Bot ist dann eh schon fertig — er läuft
ja nur kurz).

> **Wenn du das am Wochenende oder abends testest:** Die Börse ist dann zu, der
> Bot stellt das fest und macht bewusst **nichts** ("market closed",
> "Bars processed: 0"). Das ist **kein Fehler**, sondern korrektes Verhalten —
> er handelt nur, wenn die Börse offen ist.

---

## 8. Prüfen, dass alles läuft

Es gibt ein Prüf-Skript, das dir mit einem Blick sagt, ob alles gesund ist:

```bash
bash /opt/regime_trader/deploy/healthcheck.sh
```

**Das solltest du sehen** — vier Zeilen, alle unauffällig:
```
risk_halt            clear
timer                active
next_run             <ein Zeitstempel>
last_run             success   (oder not-run-yet vor dem ersten echten Lauf)
log_freshness        ok (...)
```

Wenn `timer` = `active` steht und `risk_halt` = `clear`, ist alles in Ordnung.
**Ab jetzt handelt der Bot ab dem nächsten Werktagsmorgen automatisch.**

---

## 9. Der Alltag: was du wissen musst

Du musst **nicht** täglich reinschauen, aber du solltest wissen, wie:

| Was du wissen willst | Befehl (auf dem Server) |
|---|---|
| Wann läuft er als nächstes? | `systemctl list-timers regime-trader.timer` |
| Was hat der letzte Lauf gemacht? | `journalctl -u regime-trader.service -e` |
| Was hat er insgesamt getan? | `tail -50 /opt/regime_trader/logs/app.log` |
| Welche Orders wurden gehandelt? | `cat /opt/regime_trader/logs/trades.csv` |
| Ist alles gesund? | `bash /opt/regime_trader/deploy/healthcheck.sh` |
| Bot pausieren | `sudo systemctl stop regime-trader.timer` |
| Bot wieder aktivieren | `sudo systemctl start regime-trader.timer` |

**Die eine Sache, die deine Aufmerksamkeit braucht — der Not-Aus:**

Wenn der Bot jemals **20 % Verlust vom Höchststand** erreicht, aktiviert er
eine Notbremse: Er stoppt und legt eine Sperrdatei an
(`/opt/regime_trader/logs/RISK_HALT.lock`). Ab dann macht jeder tägliche Lauf
**nichts** — absichtlich. Der Bot fährt **nicht** von selbst wieder hoch; das
soll ein Mensch (du) entscheiden.

So reagierst du darauf:
```bash
# 1. Lies, was passiert ist:
cat /opt/regime_trader/logs/RISK_HALT.lock

# 2. Wenn du entschieden hast weiterzumachen, lösche die Sperre:
sudo rm /opt/regime_trader/logs/RISK_HALT.lock
```

Der `healthcheck.sh` zeigt dir diesen Zustand als `risk_halt PRESENT`. Wenn du
willst, richte ich dir eine automatische Benachrichtigung (z.B. per
Slack/Discord) ein, damit du es sofort erfährst.

**Code aktualisieren** (wenn wir am Bot etwas verbessern): auf dem Server im
`regime_trader`-Ordner `sudo bash deploy/update.sh` ausführen — das holt die
neueste Version und übernimmt sie zum nächsten Lauf.

---

## 10. Wenn etwas nicht klappt (Fehlersuche)

**Oracle-Instanz-Erstellung zeigt "2 Errors" im Networking-Teil**
→ Neues Konto ohne Netzwerk. Bei "Primary network" **"Create new virtual
cloud network"**, bei "Subnet" **"Create new public subnet"** wählen und
**"Automatically assign public IPv4 address"** einschalten (siehe Schritt 2c).

**"Out of capacity for shape VM.Standard.A1.Flex"**
→ Kostenlose ARM-Server in deiner Region ausgebucht. Nimm den AMD-Shape
**VM.Standard.E2.1.Micro** ("Change shape" → "AMD"), oder eine andere
Availability Domain, oder versuche es später (siehe Schritt 2b).

**`ssh`: "Connection timed out"**
→ Falsche IP, oder der Server ist noch nicht fertig hochgefahren (1–2 Min
warten), oder du hast in Schritt 2 keine öffentliche IP zugewiesen, oder deine
Internetverbindung blockiert SSH.

**`ssh`: "Permission denied (publickey)"**
→ Falsche Schlüsseldatei nach `-i`, oder du hast `ubuntu@` vergessen. Der
Benutzer muss `ubuntu` sein.

**`setup.sh`: "Run as root"**
→ Du hast `sudo` vergessen. Nochmal mit `sudo bash deploy/setup.sh`.

**Testlauf zeigt "Alpaca connection verification failed" oder
"credentials missing"**
→ Die `.env` fehlt, hat Tippfehler, oder falsche Schlüssel. Prüfe mit
`sudo cat /opt/regime_trader/.env`. Achte auf versehentliche Leerzeichen um
das `=`.

**Testlauf zeigt "subscription does not permit..."**
→ Sollte mit den Tagesdaten nicht mehr vorkommen. Falls doch, sag Bescheid.

**`healthcheck.sh` zeigt `timer inactive`**
→ Timer wurde nicht gestartet. `sudo systemctl start regime-trader.timer`.

**Der Bot handelt nicht, obwohl Börse offen ist**
→ Das kann völlig richtig sein: Das Trend-System ist oft in Bargeld ("kein
Trend"). Es handelt nur, wenn sich die Zielposition ändert. Schau ins
`app.log`, dort steht die Begründung jeder Entscheidung.

Kommst du nicht weiter? Kopiere mir die genaue Fehlermeldung, dann schauen wir
gemeinsam.

---

## 11. Später: mit echtem Geld handeln

**Bitte nicht sofort.** Meine dringende Empfehlung: Lass den Bot **mehrere
Monate mit Spielgeld** laufen — idealerweise durch eine unruhige Marktphase —
und vergleiche, ob er sich so verhält wie in den Backtests. Erst dann ist der
Schritt zu echtem Geld verantwortbar.

Wenn es soweit ist, sind **drei** bewusste Änderungen nötig (vorher passiert
garantiert nichts mit echtem Geld):

1. In der `.env` echte (Live-)Schlüssel eintragen und `PAPER=false` setzen.
2. In `settings/config.py` den Wert `BROKER["mode"]` auf `"live"` ändern.
3. `sudo bash deploy/update.sh` ausführen.

Bis alle drei erledigt sind, bleibt es Spielgeld.

---

*Kurzreferenz für Fortgeschrittene: siehe [README.md](README.md) im selben
Ordner.*
