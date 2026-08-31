# Vollaudit regime_trader

Unabhängige Prüfung · Ergebnisoffen · 2026-08-24

Vollständige Rekonstruktion, kritische Strategieprüfung und Order-Lifecycle-Audit des
HMM-Regime-Traders — gegen den tatsächlich ausgeführten Code, nicht gegen seine
Dokumentation.

Branch `levered-sleeve-60-40` @ 84af7d2 · Umfang 15 953 LOC, 28 Module, 435 Tests ·
Testlauf 434 passed, 1 skipped (382 s) · Modus `BROKER["mode"] = paper`

**Status: Rot — Paper-Betrieb ja, Live-Betrieb nein.**

Die Implementierung ist deutlich sauberer als bei vergleichbaren Systemen dieser
Größe: idempotente Order-IDs, persistierter Drawdown-Zustand, Instanz-Lock,
Paper/Live-Doppelverriegelung, 434 grüne Tests. Das ist nicht der Grund für Rot.

Rot steht, weil die zentrale Renditequelle des Buchs — der 40 %-Sleeve in einem
2×-QQQ-ETF — auf einer Stichprobe validiert wurde, die das für dieses Instrument
schlimmste Marktregime strukturell nicht enthalten kann (QLD existiert erst seit
2006-06). Mit rekonstruierter Dotcom-Periode fällt der maximale Drawdown des Buchs von
−24,8 % auf −52,1 %, und der eigene −35 %-HALT feuert — mit dem im Code implementierten
Einbahn-Verhalten. Dazu kommt ein reproduzierter Pfad, auf dem ein einzelner
Datenausfall eine komplette Position liquidiert, und ein Deployment, in dem keine
Order-Ebene-Risikoprüfung läuft.

**Empfehlung:** Backtest und Paper-Trading uneingeschränkt fortsetzen. Live-Kapital
erst nach Phase 1 + 2 des Plans in Abschnitt G und nach den messbaren Kriterien in
Abschnitt J.

> Update 2026-08-24/25: K1–K3, H1–H4, M1–M5 aus diesem Report sind inzwischen
> geschlossen (Commits `63fb0ad`, `e4cac23`, `99f6690`) — siehe die jeweiligen
> Commit-Messages für den genauen Fix. Diese Datei ist als historisches Dokument
> unverändert belassen; der aktuelle Stand steht im Git-Log, nicht hier.
>
> **Korrektur 2026-08-31: K1 war am 24./25.08. NICHT geschlossen.** Die Zeile oben
> war falsch. Gemessen und dokumentiert wurde K1 damals (drei Pre-Registrierungen,
> `analysis_report_2026-08-25_k1_options.md`), entschieden wurde nichts — der Memo
> schloss selbst mit „Status of K1: **Open**", und `book_vol_target` blieb `0.0`.
> Geschlossen ist K1 seit 2026-08-31 mit `BOOK_VOL_TARGET = 0.12`, live verdrahtet
> und nicht mehr nur im Backtester: siehe `decision_2026-08-31_k1_book_vol_target.md`.
> Die in K1 unten vorgeschlagene Lösung (Buch-Vol-Target) ist damit umgesetzt; die
> zweite Hälfte des Vorschlags — „HALT-Schwelle gegen die rekonstruierte Historie neu
> setzen" — wurde bewusst NICHT umgesetzt: eine Schwelle, die auf ~60 % angehoben
> wird, damit das aktuelle Buch darunter passt, ist keine Absicherung mehr, sondern
> deren Abschaffung (Option E im Memo).

---

## A. Executive Summary

Legende: **im Code** = aus dem Quelltext belegt · **reproduziert** = in dieser Session
im Test nachgestellt · **gemessen** = quantitativ auf Daten gerechnet · **nicht
verifiziert** = Annahme ohne Beleg · **vermutet** = plausibel, ungeprüft.

1. **Der Bot ist kein HMM-Trader.** Im deployten Pfad (`BROKER["portfolio_batch_loop"]=True`)
   wird das HMM nie abgefragt. Das Buch ist ein trendgetorter Festgewichtskorb plus ein
   fest gewichteter 2×-QQQ-Sleeve. Das HMM wird bei jedem Start für jeden Ticker
   trainiert und danach ignoriert. *im Code*
2. **Der Sleeve ist auf einer Stichprobe validiert, die seinen Worst Case nicht
   enthält.** Mit synthetischem, gegen echtes QLD kalibriertem 2×-QQQ zurück bis 1999:
   Buch-maxDD −52,1 % statt −24,8 %; der −35 %-HALT feuert und beendet den Bot dauerhaft
   (CAGR 9,5 % → −0,7 %). *gemessen*
3. **Ein Datenausfall für einen einzelnen Ticker liquidiert dessen gesamte Position.**
   Fällt der Ticker aus `self._states`, fehlt er im Zielbuch, und
   `PositionTracker.diff` liest das als „Ziel 0". Reproduziert für SPY (−248 Stück) und
   IEF (−116 Stück). *reproduziert*
4. **Im deployten Pfad läuft keine Order-Ebene-Risikoprüfung.**
   `RiskManager.validate_order()` wird ausschließlich von `run_once()` aufgerufen — dem
   nicht deployten Einzelticker-Pfad. Keine Buying-Power-Prüfung, keine
   Notional-Prüfung, keine Plausibilitätsprüfung des Sizing-Preises. *im Code*
5. **Die Live-Entscheidungshistorie hat Löcher.** Der Parquet-Cache wird bis zu 5
   Kalendertage alt ausgeliefert, angehängt wird genau ein Bar. An rund 40 % der
   Handelstage fehlen 1–2 der jüngsten Sessions in SMA-200, Vol-63 und
   Korrelationsfenster. *reproduziert*
6. **Das HMM identifiziert keine handelbaren Regime.** Walk-forward auf SPY 2005–2026:
   „Bear" hat die höchste Vorwärts-Sharpe (1,34), „Crash" die niedrigste positive
   (0,23), „Bull" 0,59. Seed 42 vs. 7 stimmen an 36,9 % der Bars überein; altes vs.
   neues Modell nach einem Refit an 28,6 %. *gemessen*
7. **Kosten sind nicht das Problem, die Cash-Verzinsung schon.** Break-even gegen SPY
   liegt bei ~26 bp je Turnover-Einheit — 13× über der Annahme. Aber die
   ^IRX-Gutschrift (37 bp historisch, 119 bp bei heutigen Zinsen) ist auf dem
   Paper-Konto nachweislich nicht vorhanden. *gemessen*
8. **Die publizierten Kennzahlen sind nicht reproduzierbar.** Für dasselbe Buch
   existieren im Repo 12,95 % und 13,66 % CAGR; der Produktionscode liefert heute
   13,67 %. Es gibt kein festgeschriebenes Reproduktionsskript. *gemessen*
9. **Die 60/40-Mischung ist ein reiner Risikoregler, kein Optimum.** Über den ganzen
   Bereich Sleeve 0 → 100 % steigt CAGR monoton, fällt Sharpe monoton, fällt Calmar
   monoton. Es gibt keinen risikoadjustierten Grund für gerade 40 %. *gemessen*
10. **Es gibt genau eine Änderung, die die Projekt-eigene Vorregistrierungslatte
    besteht:** ein Vol-Target auf Buchebene. Sharpe ≥ Baseline und maxDD besser — über
    den gesamten Bereich 12–20 % und in allen vier Krisenfenstern — und es ist die
    einzige getestete Maßnahme, die das Buch in der Dotcom-Rekonstruktion unterhalb des
    eigenen HALT hält. *gemessen*

### Zentrale Frage: besitzt der Bot eine belastbare wirtschaftliche Grundlage?

Teilweise — und deutlich schwächer, als die publizierten Zahlen nahelegen. Der Kern
(trendgetorter Multi-Asset-Korb) ist eine solide, ökonomisch begründbare Strategie:
Sharpe 1,04 bei 7,6 % Vol und −11,7 % maxDD über 19 Jahre. Der Sleeve ist gekauftes
Beta mit einem in der Stichprobe nicht sichtbaren Tail. Nach Kosten schlägt das
Gesamtbuch SPY um 2,6 pp p. a. bei halbem Drawdown — aber es schlägt QQQ buy-and-hold
nicht (13,7 % vs. 16,5 % CAGR), und das 90-%-Bootstrap-Intervall der Sharpe [0,48 –
1,22] überlappt SPY (0,63) zu 16 %.

| Metrik | Wert |
|---|---|
| CAGR netto | 13,7 % (SPY 11,1 % · QQQ 16,5 %) |
| Sharpe | 0,86 (90 %-CI [0,48 · 1,22]) |
| maxDD Stichprobe | −24,8 % (2007-04 … 2026-08) |
| maxDD mit Dotcom | −52,1 % (rekonstruiert, 2000–2026) |
| Break-even Kosten | 26 bp (Annahme: 2 bp) |
| Turnover | 9,4 (Σ\|Δw\| p. a., 14,6 % Handelstage) |

Alle Kennzahlen unabhängig nachgerechnet auf `data_cache/yahoo/` (SPY, QQQ, GLD, IEF,
QLD; dividenden- und splitbereinigt), Ausführungsmodell „Entscheidung T−1 Close → Fill
T Open", 2 bp je Turnover-Einheit, ^IRX auf Leerlauf-Cash. Die Nachbildung stimmt
gewichtsweise auf 3·10⁻¹⁷ mit `core/portfolio_backtester.py` überein.

---

## B. Verifizierte Systemarchitektur

Der Bot läuft als systemd-Timer-Oneshot: Mon-Fri 09:35 America/New_York → `python
main.py --once`. Ein Prozess pro Handelstag, ca. 15 s Laufzeit, kein persistenter
Zustand außer Lockfile, Risikozustand und Datencache.

### Tatsächlich ausgeführter Pfad

1. **Daten** — Historie: `MarketDataFeed.get_training_data` → Alpaca IEX,
   `adjustment=ALL`, 2 J
2. **Cache** — Parquet: `_load_cache`, Grace 5 Tage
3. **Bar** — Neuester Close: `get_latest_bar(completed_only=True)`
4. **Signal** — SMA-200: `is_trend_confirmed(close)`
5. **Sicht** — AssetViews: `universe.build_views`, vol63
6. **Filter** — Korr-Selektor: `select_decorrelated_views`, \|ρ\|>0.80
7. **Gewicht** — Inverse Vol: `allocator.target_weights`, Klassencaps
8. **Buch** — Sleeve-Komposition: `sleeves.compose_book`, 0.60·Kern + 0.40 QLD
9. **Risiko** — Buchprüfung: `RiskManager.validate_book`
10. **Sizing** — Stückzahl: `shares_for_target_weight(w, live-Preis, equity)` — *keine
    Order-Ebene-Risikoprüfung*
11. **Order** — Delta-Markt: `OrderExecutor.rebalance` → COID
    `rt-{T}-{Datum}-{side}` — *keine Order-Ebene-Risikoprüfung*
12. **Fill** — Polling: `await_fills(timeout=90)`
13. **Sync** — Abgleich: `positions.refresh` + `_check_portfolio_drift`

Der HMM-Pfad kommt in dieser Kette nicht vor.

### Was nicht läuft

| Komponente | Status im Deployment | Beleg |
|---|---|---|
| `HMMEngine` | bei jedem Start je Ticker trainiert (BIC über k=3…7), danach nie abgefragt | `main.py:203-245` trainiert, `run_portfolio_once` liest `state.engine` nie |
| `RegimeOrchestrator` | instanziiert, nie `evaluate()`-t | nur `run_once`, Pfad deaktiviert |
| `ORCHESTRATOR["vol_target"]=0.15` | tot | im Config bereits dokumentiert (2026-08-13) |
| `ORCHESTRATOR["trend_confirm_bars"]=3` | tot | dito |
| `RiskManager.validate_order` | tot | `main.py:780` — einziger Aufrufer ist `run_once` |
| `REGIME_LEVERAGE_CAPS` | per Flag deaktiviert | `use_regime_leverage_caps=False` |
| `submit_stop_loss`, `modify_stop` | nirgends aufgerufen | repoweite Suche: 0 Treffer außerhalb der Definition |
| Tages-Breaker HALVE/FLATTEN | deaktiviert | `cb_daily_enabled=False` |
| Stop-Loss / Take-Profit | 0.0 (gemessen abgeschaltet) | `experiments_report_stops.md` |

Das ist kein Vorwurf — die Abschaltungen sind jeweils gemessen begründet. Es ist aber
eine Feststellung über den Ist-Zustand: von den in der Aufgabenstellung genannten
Komponenten trägt im Live-Betrieb nur ein Bruchteil zur Entscheidung bei, und der Name
des Projekts beschreibt nicht mehr, was es tut.

### Stellen, an denen interner Zustand und Brokerzustand auseinanderlaufen können

| Stelle | Mechanismus | Auffangnetz |
|---|---|---|
| Start ohne einen Ticker | Ticker fehlt in `_states` → Ziel 0 → Verkauf | keins — K2 |
| Cancel → Submit | Cancel unbestätigt, alte Order kann noch fillen | keins — H2 |
| Netzwerk-Timeout nach Annahme | Retry mit gleicher COID → Duplikat-Reject → `submit_order` gibt "" zurück, Order lebt aber | `cancel_all_open_orders` beim Shutdown |
| Teilfüllung + Timeout | `await_fills` kennt `filled_qty`, Aufrufer verwirft es | `positions.refresh()` danach |
| Teilfüllung + zweiter Lauf am selben Tag | COID = (Ticker, Datum, Seite) → Rest nie nachgeordert | nächster Handelstag |
| Absturz mitten im Rebalance | Prozess weg, Orders leben weiter | nächster Start: refresh + cancel_all_open_orders |
| Stale Equity | `_current_equity` Fallback | `_equity_stale` blockiert Submit — korrekt gelöst |
| Corporate Action im Cache-Fenster | Cache hält alte Bereinigungsbasis, neuer Bar die neue | keins — M5 |
| Rundung beim Schließen | `round(qty,4)` kann über den Bestand hinaus runden | keins — M2 |

---

## C. Befunde nach Schweregrad

### Kritisch — möglicher Kapitalverlust oder unbeabsichtigte Position

**K1 — Der Sleeve ist auf einer Stichprobe validiert, die sein schlimmstes Regime
nicht enthalten kann.** QLD wurde am 2006-06-21 aufgelegt — nach dem Tech-Crash, in
dem QQQ ~83 % verlor. Jede publizierte Sleeve-Zahl stammt aus 2007–2026. Die Schwelle
`cb_max_drawdown_halt = 0.35` ist ausdrücklich damit begründet, „~10 pp Kopffreiheit
über allem Beobachteten" zu lassen — beobachtet wurde ein Fenster ohne 2000–2002.
Nachgebaut mit synthetischem 2×-QQQ: 2000–2026 rekonstruiert liefert Sleeve-Sharpe
0,49 / DD −80,1 %, Buch 60/40 Sharpe 0,60 / DD −52,1 %. Kalenderjahr 2000: Buch −17,8 %
(DD −36,4 %) vs. SPY −9,7 %. Der SMA-200 hat 2000 nicht geschützt — QQQ oszillierte im
ersten Halbjahr um seine SMA, während er fiel. Und dann feuert der eigene HALT: bei
−35 % kollabiert die Gesamt-CAGR von +9,52 % auf −0,70 %, weil der HALT im Code eine
Einbahnstraße ist. **Lösung:** Buch-Vol-Target (Abschnitt F/H5) — hält den DD in der
Rekonstruktion bei −34,7 % und verhindert das HALT-Auslösen; und HALT-Schwelle gegen
die rekonstruierte Historie neu setzen statt gegen die vorhandene.

**K2 — Ein Datenausfall für einen Ticker liquidiert dessen gesamte Position.**
`startup()` überspringt jeden Ticker, dessen Historie zu kurz ist oder dessen
HMM-Training eine Exception wirft. Der Ticker fehlt danach in `self._states`.
`_compute_live_target_book()` baut das Buch nur aus `_states`, und
`PositionTracker.diff()` liest einen fehlenden Zielschlüssel als 0.0. Reproduziert:
Ausfall SPY → SPY 247,99 → 0 Stück (Verkauf); Ausfall IEF → IEF 116,29 → 0 Stück;
Ausfall QQQ → Sleeve-Signal fehlt → QLD-Ziel 0 (40 % des Buchs). **Lösung:** Startup
als Alles-oder-nichts behandeln: fehlt ein validierter Ticker, gar nicht handeln (Exit
1). HMM-Training vom Handelspfad entkoppeln — es darf einen Ticker nicht disqualifizieren.

**K3 — Der deployte Pfad hat keine Order-Ebene-Risikoprüfung.**
`RiskManager.validate_order()` — Docstring: „Every order must pass through
`validate_order()` before it can reach the broker" — wird ausschließlich von
`run_once()` aufgerufen, dem per `portfolio_batch_loop=True` abgeschalteten
Einzelticker-Pfad. Im deployten Pfad wird nur `validate_book()` auf die Gewichte
angewandt; zwischen dieser Prüfung und dem Broker liegen zwei ungeprüfte Schritte:
`shares_for_target_weight` akzeptiert jeden Preis > 0 ungeprüft gegen Equity, und
`OrderExecutor.rebalance()` hat keine Buying-Power- oder Notional-Prüfung. **Lösung:**
Sanity-Band um den Entscheidungs-Close auf den Sizing-Preis; Notional- und
Buying-Power-Prüfung im Portfolio-Pfad vor `rebalance()`.

### Hoch — wahrscheinlich falsche Backtestergebnisse oder instabiler Live-Betrieb

**H1 — Die Live-Entscheidungshistorie hat Löcher — Live ≠ Backtest.** `_load_cache`
liefert den Cache aus, solange er höchstens 5 Kalendertage hinter dem Anfrageende
liegt; der `--once`-Prozess hängt danach genau einen Bar an und persistiert nichts.
Reproduziert über 40 aufeinanderfolgende Handelstage: an 16 von 40 Tagen fehlten 1–2
der jüngsten abgeschlossenen Sessions in SMA-200, Vol-63 und Korrelationsfenster.
**Lösung:** Cache als Basis behalten, fehlenden Schwanz gezielt nachladen und mergen.

**H2 — Cancel ohne Bestätigung, und eine Idempotenzschlüssel-Granularität, die den
Rest-Trade blockiert.** (a) `cancel_open_orders_for_ticker()` kehrt zurück, sobald der
Cancel gesendet ist — Alpaca quittiert mit 204 und setzt die Order auf
`pending_cancel`, nicht terminal, ohne dazwischenliegenden Statusabruf. (b) COID =
`rt-{Ticker}-{Bar-Datum}-{Seite}` ist gröber als eine Entscheidung: ein teilgefülltes
Rebalance kann den Rest am selben Tag nie nachordern (Duplikat-Reject). Der
Duplikat-Fehler wird zudem wie ein transienter behandelt und dreimal wiederholt.
**Lösung:** nach Cancel bis zum terminalen Status pollen; COID um die Zielmenge
erweitern; nach Duplikat-Reject `get_order_by_client_id` abfragen statt "" zurückzugeben.

**H3 — Die publizierten Kennzahlen sind nicht reproduzierbar.** Für dasselbe Buch,
denselben Zeitraum und dieselbe Datenquelle existieren im Repo zwei Zahlen (13,66 %,
12,95 %), und der heutige Produktionscode liefert eine dritte (13,67 %). Kein
festgeschriebenes Reproduktionsskript. **Lösung:** ein Skript, das Datenstand (Hash),
Kostenannahmen, Cash-Modell und Span ausdruckt und die Kennzahlen erzeugt.

**H4 — Zwei Annahmen im Kostenmodell sind unbelegt.** Slippage 2 bp:
`scripts/slippage_check.py` findet 127 ORDER-Zeilen und 0 FILL-Zeilen — bis heute keine
gemessene Ausführung. Cash-Verzinsung: 17 FILL-, 0 INT-Aktivitäten auf dem Paper-Konto
über zwei Monate — der Leerlauf-Cash verzinst sich nicht. Bei 24,2 % mittlerer
Cashquote ist der Zinsposten mit 119 bp p. a. bei heutigen Sätzen der größte einzelne
Renditehebel im gesamten Bericht.

### Mittel — Rendite-, Robustheits- oder Betriebsverbesserung

**M1 —** Das HMM wird bei jedem Start trainiert, aber im deployten Pfad nie befragt;
ein Trainingsfehler kann über die Exception-Klausel in `startup()` einen Ticker aus
dem Buch werfen (K2). **M2 —** `round(qty, 4)` kann eine schließende Verkaufsorder über
den Bestand hinaus runden. **M3 —** Keine Spread-/Liquiditäts-/Preisplausibilitätsprüfung;
IEX-Einzelprint als Sizing-Preis. **M4 —** Retry-Klassifikation zu grob (nur 401/403
non-transient); 429 ohne `Retry-After`-Auswertung; kein Jitter. **M5 —** Rückwirkend
revidierte Bereinigungsfaktoren (`adjustment=ALL`) — im Cache und im Backtest. **M6 —**
Kein Linter/Type-Checker, keine Abhängigkeits-Pins; CI installiert etwas anderes als
`requirements.txt` (fehlt: alpaca-py, pyarrow, requests, python-dotenv — der
Parquet-Cache-Pfad und die Alpaca-Request-Konstruktion sind in CI nie exerziert).
`mypy --ignore-missing-imports` meldet 177 Fehler in 19 Dateien.

### Niedrig

`cancel_open_orders_for_ticker` storniert jede offene Order des Symbols ohne
Typ-/Präfix-Filter. `get_orders()` ohne Filter/Paginierung. `_handle_gaps` forward-fillt
Preislücken. Toter Code: `submit_stop_loss`, `modify_stop`, `VolatilityRanker` im
deployten Pfad. `tests/test_main.py:265` überspringt sich selbst.

### Nicht empfohlen

Rebalancing-Bänder/No-Trade-Zonen, andere Allokationsmathematik (Kelly,
Momentum-Velocity, HRP), Drawdown-sensitive Risikoreduktion, ein pauschaler
Retry-Dekorator über alle Exceptions — alle vier gemessen null oder negativ, bzw. ein
Rückschritt gegenüber der vorhandenen COID-gebundenen Retry-Konstruktion.

---

## D. Strategie- und HMM-Bewertung

Die wirtschaftliche Hypothese, ehrlich formuliert: ein langsamer Trendfilter
(SMA-200) trennt Perioden mit niedriger von Perioden mit hoher Realvolatilität gut
genug, um über mehrere Assetklassen hinweg Drawdown zu vermeiden; das dadurch
freiwerdende Risikobudget wird über ein gehebeltes Aktienprodukt zurückgekauft.

**Kann das HMM Regime prognostizieren? — gemessen: nein.** Walk-forward auf SPY
2005–2026, 44 Refits: „Bear" hat die höchste Vorwärts-Sharpe (1,34), „Crash" die
niedrigste positive (0,23) — die Reihenfolge ist gegenüber der ökonomischen Bedeutung
der Labels praktisch invertiert. Zum Vergleich, SMA-200-Zustand: über SMA-200
Vorwärts-Vol 11,4 %, unter SMA-200 28,2 % — Verhältnis 2,5:1. Die SMA-200 trennt nach
**Risiko**, nicht nach Rendite (unterhalb liegt die mittlere Vorwärtsrendite sogar
höher). Das ist der Mechanismus, über den die Strategie funktioniert:
Volatilitäts-Timing, nicht Renditeprognose.

**Stabilität:** Seed 42 vs. 7 stimmen an 36,9 % der Bars überein; altes vs. neues
Modell nach einem Refit an 28,6 % (Zufall bei 3-7 Klassen: 14-33 %). Die BIC-Auswahl
wechselt die Zustandszahl an 60 % der Refit-Übergänge. Positiv: die
Look-ahead-Vermeidung im HMM ist korrekt implementiert (reiner Forward-Pass, gefilterte
nicht geglättete Wahrscheinlichkeiten).

**Vergleich mit einfachen Baselines:** ein naiver gleichgewichteter Trendkorb plus
derselbe Sleeve — ohne Korrelationsselektor, ohne inverse Vol — liefert Sharpe 0,90 vs.
0,86 der Produktion, besseren Drawdown, bei 59 bp weniger CAGR. „SMA-200 + 15 %
Vol-Target auf QQQ" erreicht Sharpe 0,89 bei gleichem Drawdown, mit einem Ticker.

**Krisenfenster:** In 5 von 7 getesteten Krisenfenstern verliert das Buch mehr als
SPY. Nur die GFC (der einzige langsame Bärenmarkt der Stichprobe) trägt den Ruf der
Strategie; schnelle, scharfe Ereignisse (EU-Krise 2011, Aug 2015, Q4 2018, COVID,
Zölle 2025) schneiden durchweg schlechter ab als der Index.

---

## E. Backtest-Integrität

Kein Look-ahead in Features, HMM, Scaler oder Ausführungsmodell — geprüft und sauber.
Der eigentliche Bias sitzt eine Ebene höher: die Strukturentscheidungen selbst
(Instrument, Gewicht, Schwelle) wurden auf demselben Datensatz getroffen, gegen den sie
berichtet werden. Empfehlung: unberührtes finales Holdout (siehe `HOLDOUT_START` in
`settings/config.py`, gesetzt 2026-08-24), Strukturvalidierung auf rekonstruierter
Historie für unterfinanzierte Instrumente, Vorregistrierung mit fixer
Entscheidungsregel vor jedem Lauf.

**Kostenmodell:** Break-even gegen SPY liegt bei ~26 bp je Turnover-Einheit — die
Strategie ist nicht kostenfragil (13× Puffer über der 2bp-Annahme). Die eigentliche
Kostenlücke ist der Unterschied zwischen Paper- und Live-Fills, mit null Datenpunkten
belegt.

---

## F. Renditehypothesen

Priorisierte Liste (Entscheidungsregel: Sharpe ≥ Baseline UND maxDD nirgends
schlechter, über die volle Spanne und in jedem der vier Krisenfenster):

1. **Buch-Vol-Target 20 %** — Sharpe +0,02, DD −0,6pp, COVID +6,3pp, verhindert das
   HALT im Dotcom-Szenario; Kosten: −64 bp CAGR. Overfitting-Risiko niedrig (flache
   Fläche 12-20 %, bekannter Mechanismus). **Empfohlen, Holdout-Bestätigung ausstehend.**
2. Cash-Verzinsung real machen — bis +119 bp p.a.; Owner-Entscheidung, kein
   Signalparameter.
3. Sleeve-Gewicht senken (0,40 → 0,30) — Sharpe +0,03, DD −3,4pp; reiner Regler,
   Owner-Entscheidung.
4. HALT-Schwelle gegen rekonstruierte Historie neu setzen — nur zusammen mit #1.
5. Sleeve-Signal SPY statt QQQ — in-sample besser, aber klassischer In-sample-Fund;
   nur mit Vorregistrierung.
- Hysterese, Bänder, DD-Overlay, andere Allokationsmathematik — gemessen null oder
  negativ, abgelehnt.

**Zur Hysterese-Hypothese der Aufgabenstellung:** bereits implementiert
(`_CONFIRM_BARS = 3`, strenger als die vorgeschlagenen 2). Eine 0,65-Schwelle würde
kaum filtern (Ø Konfidenz 0,970). Die Behauptung „bis zu 40 % der schlechten Trades
verhindert" hat im Repository keine Quelle und ist nicht reproduzierbar — zurückgewiesen.

---

## G. Minimaler Umsetzungsplan

- **Phase 0 — Reproduzierbarkeit:** Reproduktionsskript, Holdout einfrieren,
  requirements.txt pinnen + CI korrigieren, H3-Widerspruch auflösen.
- **Phase 1 — Kapitalsicherheit:** K2, K3, M2.
- **Phase 2 — Broker-/Betriebsrobustheit:** H1, H2, M4.
- **Phase 3 — Valide Strategieverbesserung:** Vol-Target implementieren (Standard
  No-Op), vorregistrierte Entscheidungsregel committen, dann auf dem Holdout auswerten;
  bei Annahme HALT-Schwelle neu setzen — nicht vorher.
- **Phase 4 — Paper-Trading-Validierung:** ≥60 Handelstage, Slippage/Cash-Zins
  messen, täglicher Reconciliation-Report, ein simulierter Absturz mitten im Rebalance.

---

## H–J. Code-Diffs, Tests, Go/No-Go

Die in Abschnitt H ausformulierten Patches (K2, K3a, H2b, H1) sowie Abschnitt I
(Testlücken) und J (Go/No-Go-Kriterien) sind in den Commits umgesetzt, die auf diesen
Report folgen — siehe Git-Log ab `63fb0ad`. Die vollständigen Diff-Texte aus der
Original-Session sind nicht separat archiviert; der tatsächlich angewandte Code ist
die verbindliche Fassung.

**Die Entscheidung in einem Satz:** Der Bot ist handwerklich gut gebaut und operativ
gut abgesichert, aber sein wichtigster Renditebaustein ist auf einer Stichprobe
validiert, die seinen Worst Case nicht enthalten kann — und der eingebaute
Schutzmechanismus würde in diesem Worst Case den Bot dauerhaft abschalten statt ihn zu
schützen. Solange das nicht adressiert ist, gehört kein echtes Geld dahinter.

Keine Aussage in diesem Bericht ist ein Renditeversprechen. Jede Zahl ist eine Messung
auf historischen Daten unter benannten Annahmen; die belastbarste Aussage über die
Zukunft ist das 90 %-Bootstrap-Intervall der Sharpe, [0,48 · 1,22], und es überlappt
den Index.
