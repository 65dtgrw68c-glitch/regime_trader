# Tiefenanalyse regime_trader — 2026-08-01

Auftrag: Schwachstellen, Risikominimierung, Renditesteigerung. Alle Zahlen unten sind
in dieser Session gemessen (Yahoo adjusted, 30y), nicht aus den bestehenden Reports
übernommen. Messskripte: siehe Anhang.

---

## 0. Eckdaten (aus dem Code abgeleitet — die Vorlage war unausgefüllt)

| | |
|---|---|
| Assets | SPY, QQQ (equity), GLD (gold), IEF (bonds), alle `validated: True`; DBC vorhanden, nicht validiert |
| Timeframe | Tagesbalken. Eine Entscheidung/Tag, systemd-Timer 09:35 NY (`deploy/regime-trader.timer`) |
| Live-Logik | SMA-200-Trendfilter je Asset → Korrelationsselektor (60d, \|ρ\|>0.80) → Inverse-Vol innerhalb Klassenbudgets (equity 0.70 / gold 0.20 / bonds 0.25) → per-Name-Cap 0.50, Brutto-Cap 1.00, Rest Cash |
| Validierte Logik | `RegimeOrchestrator`: trend_core + trend_confirm_bars=3 + vol_target=0.15, cap 0.50/Name — **läuft nicht live** (Befund 0) |
| Risiko | Stops/TP = 0.0 (evidenzbasiert aus), Tages-Breaker aus, Wochen-Breaker + -20% HALT nominell an |
| Broker | Alpaca Paper, Market Orders, fraktionale Stückzahlen |

---

## 1. Schwachstellen- & Risikoanalyse

### Befund 0 — Es laufen wieder zwei verschiedene Systeme (kritisch)

`main.py:322` schaltet den Live-Pfad über
`config.BROKER.get("portfolio_batch_loop", True)`. **Der Key existiert in `config.BROKER`
nicht** → Default `True` → es läuft `run_portfolio_once` (Allokator-Pfad).

Verifiziert: in `run_portfolio_once` (main.py:334-511) gibt es **keinen einzigen Aufruf**
von HMM, FeatureEngineer oder RegimeOrchestrator. `ORCHESTRATOR` wird nur in
`main.py:223` (Startup, Objekt wird nie benutzt) und in den Report-Skripten konsumiert.

Konsequenzen:
* `trend_confirm_bars=3` und `vol_target=0.15` — das pre-registrierte Profil, für das
  die gesamte v3/v4/30y-Evidenz existiert — sind **live wirkungslos**.
* Der HMM wird beim Start trainiert und danach nie wieder angefasst. Der Namensgeber
  des Projekts ist toter Code im Betrieb.
* Es gibt keinen Rebalance-Trigger, keine Drift-Schwelle, keine Hysterese im Live-Pfad.

Das ist derselbe Klassenfehler wie beim Überholung 2026-07-10 („drei verschiedene
Systeme"), nur an neuer Stelle.

### Befund 1 — Der Backtester des Live-Pfads verwirft die Overnight-Rendite (kritisch)

`core/portfolio_backtester.py:193-205` setzt im Modus `next_open` für **jede** Position
`entry = open[t]`, `exit = close[t]` — unabhängig davon, ob die Position schon seit
gestern gehalten wurde. Die Lücke `close[t-1] → open[t]` wird nie gutgeschrieben.

Gemessen (1996-2026, adjusted):

| Symbol | Gesamt p.a. | nur intraday | nur overnight |
|---|---:|---:|---:|
| SPY | 10.37% | **0.47%** | 9.85% |
| QQQ | 10.67% | **-2.83%** | 13.89% |
| GLD | 10.31% | -0.13% | 10.46% |
| IEF | 3.54% | 1.85% | 1.66% |

Effekt auf den Live-Pfad end-to-end (2004-11 … 2026-07, 4 Assets):

| | CAGR | Sharpe | maxDD |
|---|---:|---:|---:|
| wie heute codiert (= Ausgabe von `main.py --backtest`) | 1.46% | 0.28 | -13.25% |
| Ausführung korrigiert | 8.26% | 1.09 | -11.50% |

Der Fehler macht das Ergebnis **zu schlecht**, nicht zu gut — es ist also kein
Schönfärberei-Bug. Aber: jede Entscheidung, die gegen dieses Tool getroffen wurde, ist
wertlos, weil es gute von schlechten Änderungen nicht unterscheiden kann. Ironie am
Rande: der als „Legacy" geführte `close_to_close`-Modus ist der korrektere von beiden,
und `next_open` wurde im Master-Report als *akzeptierte Verbesserung* geführt.

### Befund 2 — In der deployten Betriebsart gibt es faktisch keine Circuit Breaker (kritisch)

`deploy/regime-trader.service` startet `main.py --once`, `Type=oneshot`, täglich neu.
Der `RiskManager` hält `_peak_equity` und `_equity_history` nur im RAM; `startup()`
ruft `start_new_day(equity)` und setzt damit den Peak auf das *heutige* Equity.

Simulation eines -45%-Absturzes über 12 Sessions gegen den echten `RiskManager`:

| Betriebsart | gemessener Drawdown | schlimmster Breaker | Lock-File |
|---|---|---|---|
| `main.py --once` (deployed) | konstant **0.00%** | **NONE** | nein |
| `main.py` (Dauerlauf) | bis -45% | HALT bei -21% | ja |

Zusammen mit `cb_daily_enabled: False` heißt das: **von vier Circuit Breakern ist im
Betrieb keiner aktiv.** Der Wochen-Breaker feuert ebenfalls nie, weil `end_of_day()`
im frischen Prozess nie erreicht wird (`_risk_day is None`). Verbleibender Schutz:
nur der SMA-200-Ausstieg der Strategie selbst plus `validate_book`.

### Befund 3 — Die veröffentlichte Kennzahl beschreibt ein anderes Portfolio (kritisch)

`experiments_report_portfolio_breakers.md` nennt +15.4% CAGR / Sharpe 1.00 / DD -20.3%.
Methode (`scripts/portfolio_check.py:260`): `r_joint = Σ r_i − y*(n−1)`, wobei jedes
`r_i` ein Einzel-Backtest **mit cap 0.50** ist. Vier Namen à 0.50 → **Brutto bis 2.0×**.
Das verletzt `gross_cap = 1.0` und `max_leverage = 1.0` und wäre auf dem Konto ohne
Margin nicht darstellbar.

Der tatsächlich deployte Pfad läuft bei ~73% mittlerem Brutto → ~8% CAGR.
**Erwartungslücke rund 7 Prozentpunkte p.a.**

### Befund 4 — QQQ wird faktisch nicht gehandelt

Der Korrelationsselektor (60d, \|ρ\|>0.80) sortiert nach Vol aufsteigend und wirft den
zweiten Aktien-Namen raus. Gemessen 2004-2026:

* mittleres Gewicht SPY **37.3%** (an 75% der Tage gehalten), QQQ **5.4%** (13% der Tage)
* beide gleichzeitig nur an **4.0%** der Tage
* häufigste Auswahl: `(GLD, IEF, SPY)` an 36.1% der Tage

Das gesamte „SPY+QQQ-Kern"-Narrativ ist live ein SPY-Sleeve. Zusätzlich: weil
`per_name_cap` 0.50 < `class_caps.equity` 0.70 ist und meist nur *ein* Aktien-Name
durchkommt, bleiben **20 Punkte Aktienbudget dauerhaft ungenutzt**.

### Befund 5 — „1% Risiko pro Trade" existiert nicht

`RISK["max_risk_per_trade"] = 0.01` und `RiskManager.size_position()` werden von keinem
Live- oder Backtest-Pfad aufgerufen (nur Definition + Docstring). Die Sizing-Regel ist
ausschließlich `shares_for_target_weight()`, also gewichtsbasiert. Die Konfiguration
verspricht eine Regel, die es nicht gibt.

### Befund 6 — Live/Backtest-Timing-Divergenz

Timer feuert 09:35 NY. `MarketDataFeed.get_latest_bar` (market_data.py:104-120) liefert
den letzten `1Day`-Balken — um 09:35 also den **noch unfertigen heutigen Tagesbalken**.
SMA-200 und Vol werden damit auf einem 5-Minuten-„Tagesschluss" berechnet, die Order
geht sofort als Market Order raus. Der Backtest entscheidet auf dem fertigen Close und
füllt am nächsten Open. Kein Look-Ahead, aber ein abweichendes, ungetestetes Timing.

### Befund 7 — Klassen-Cap-Bug im Einzel-Asset-Pfad (falls je umgeschaltet wird)

`main.py:672-689` baut `candidate_weights` aus dem Zielgewicht des aktuellen Tickers
plus den Ist-Gewichten der anderen. Steht SPY bei 0.50 und QQQ will 0.50, ergibt das
equity = 1.00 > `class_caps.equity` = 0.70 → `rejected_by_risk`. Der zuerst iterierte
Ticker gewinnt dauerhaft; die Reihenfolge entscheidet, wer handeln darf.

### Befund 8 — Turnover und Kostensensitivität

* aus Gewichtsänderungen: **11.9× Buch/Jahr**
* zusätzlich ~0.8×/Jahr aus dem täglichen Nachziehen der Stückzahlen — `rebalance()`
  läuft in jedem Bar mit 1e-5-Schwelle → **Orders an 97.5% der Handelstage**
  (~246 Ordertage/Jahr statt ~36)
* **107 Tage (2.0%) mit >50% Tagesumschlag** — Selektor-Flips, die bis zu 140% des
  Buches auf einmal per Market Order umschichten

Kostensensitivität (Slippage pro Einheit Turnover):

| bps | 0 | 2 | 5 | 10 | 20 | 50 |
|---|---:|---:|---:|---:|---:|---:|
| CAGR | 8.26% | 8.01% | 7.62% | 6.99% | 5.73% | 2.03% |
| Sharpe | 1.09 | 1.06 | 1.01 | 0.93 | 0.77 | 0.30 |

Der Edge lebt von billiger Ausführung. Damit ist die noch offene Slippage-Messung
(Phase 3) die wichtigste offene Messung überhaupt.

### Befund 9 — 367 Tests grün, keiner deckt irgendetwas davon ab

### Der positive Befund

Richtig gemessen ist der deployte Pfad **gut**. 2004-11 … 2026-07, 2 bps, 2% Cash:

| | CAGR | Vol | Sharpe | maxDD | Calmar |
|---|---:|---:|---:|---:|---:|
| Live-Pfad (Ausführung korrigiert) | 8.01% | 7.53% | **1.06** [0.71, 1.40] | **-11.62%** | 0.69 |
| Equal-Weight Buy & Hold | 11.06% | 11.17% | 0.99 | -28.01% | 0.39 |

Krisenverhalten (Strategie vs. EW-B&H): GFC 2007-11…2009-03 **-0.7%** vs -16.2%;
2022-Bär **-5.0%** vs -18.4%; COVID -5.7% vs -0.5% (Whipsaw-Kosten: raus am Tief,
Rebound verpasst); Q4-2018 -6.9% vs -5.0%.
Verlustjahre: 2008 -0.9%, 2015 -7.2%, 2022 -6.9%.
Sleeve-Beitrag: SPY 56.5%, GLD 24.4%, QQQ 12.1%, IEF 7.0%. Ohne GLD fällt Sharpe auf
0.94 — das Gold-Sleeve trägt real, ist aber auch ein 21-Jahre-Gold-Bullenmarkt-Effekt.

---

## 2. Sicherheitsoptimierung (Capital Protection)

### P0 — sofort, keine Strategieänderung

1. **Breaker-State persistieren.** `peak_equity` + `equity_history` in eine JSON neben
   dem Lock schreiben und beim Start laden (oder aus Alpacas `portfolio-history`
   rekonstruieren). Ohne das ist der -20%-Halt Dekoration. Regressionstest: der
   Lifecycle-Test aus dieser Analyse (fresh process pro Tag, -45%-Pfad → HALT muss feuern).
2. **Einen Live-Pfad festlegen und den anderen löschen.** Solange beide existieren,
   driften sie wieder auseinander. Empfehlung: **Allokator-Pfad behalten** — er ist
   risikoadjustiert besser als das validierte Orchestrator-Buch (Sharpe 1.06 / DD -11.6%
   gegen 1.00 / -20.3%). Den Orchestrator/HMM-Stack entfernen oder explizit als
   „nicht live" markieren und die Reports auf den Allokator-Pfad umstellen.
3. **`portfolio_batch_loop` explizit in `config.BROKER` eintragen.** Ein
   Verhaltensschalter darf nicht nur als `.get()`-Default existieren.
4. **Selektor-Hysterese.** Ein Name fliegt erst raus, wenn er N Tage in Folge abgelehnt
   wird. Ziel ist nicht Rendite, sondern die 107 Tage mit >50% Tagesumschlag zu
   entschärfen — das ist reines Ausführungs-Tailrisiko.
5. **Daten-Sanity-Kill-Switch.** Letzter Balken älter als 3 Handelstage, \|Preissprung\|
   > 20%, Equity/Positions-Inkonsistenz → nicht handeln, alarmieren. Der Bot handelt
   derzeit auf allem, was der Feed liefert.

### Explizit NICHT tun (evidenzbasiert)

* **Keine ATR-/Prozent-Stops.** `experiments_report_stops.md` (28y, SPY+QQQ): das
  2%/4%-Paar kostet 0.12-0.19 Sharpe bei *schlechterer* DD; kein Level zwischen 2% und
  15% schlägt die Baseline. Für ein Tagesbalken-Trendsystem *ist* der SMA-Ausstieg der
  Stop. Das gilt unverändert.
* **Tages-Breaker (-2%/-3%) nicht reaktivieren.** Sie messen Close-zu-Close, feuern also
  nach realisiertem Verlust und verkaufen das Tief.
* **Klassen-Caps, SMA-Fenster, Korrelationsschwelle nicht nach Backtest-Sharpe nachziehen.**

---

## 3. Renditeoptimierung — was getestet wurde und was nichts brachte

Alle Varianten mit korrigierter Ausführung + 2 bps, identischer Span.

### Variante A (Evolution)

| Variante | CAGR | Sharpe | maxDD | Calmar |
|---|---:|---:|---:|---:|
| 0 Baseline (= heutiger Live-Pfad) | 8.01% | **1.06** | **-11.62%** | **0.69** |
| 1 ohne Korrelationsselektor | 9.33% | 1.01 | -14.37% | 0.65 |
| 2 Rebalance-Band 5% | 8.01% | 1.06 | -11.62% | 0.69 |
| 3 Portfolio-Vol-Target 10% | 7.40% | 1.04 | -11.34% | 0.65 |
| 4 ungenutztes Klassenbudget verteilen | 8.89% | 0.97 | -14.58% | 0.61 |
| 5 alles kombiniert | 8.92% | 0.97 | -15.81% | 0.56 |

**Keine einzige Variante schlägt die Baseline risikoadjustiert.** Bemerkenswert:
* Der Korrelationsselektor *hilft* (1.06 vs 1.01) — meine Ausgangsvermutung war falsch.
* Das Rebalance-Band ändert **nichts** (Turnover 11.9 → 11.8). Der Turnover kommt aus
  wenigen großen Sprüngen, nicht aus Drift — das Band ist nicht der Hebel.
* Vol-Targeting bringt nichts, weil Inverse-Vol + Klassen-Caps die Vol-Kontrolle bereits
  leisten.

### Exposure-Hebel (Brutto ≤ 1.0, kein Margin)

| k | mittl. Brutto | CAGR | Sharpe | maxDD | Calmar |
|---:|---:|---:|---:|---:|---:|
| 1.00 | 73.0% | 8.01% | 1.06 | -11.62% | 0.69 |
| 1.30 | 84.4% | 8.84% | 1.01 | -14.73% | 0.60 |
| 1.50 | 88.3% | 9.07% | 0.98 | -15.82% | 0.57 |
| 1.70 | 90.4% | 9.14% | 0.96 | -16.67% | 0.55 |

+1.1pp CAGR kostet +5pp Drawdown, Calmar fällt monoton. **Innerhalb der
Nicht-Hebel-Grenze ist das Buch nahe seinem effizienten Punkt.**

### Variante B (Revolution) — Universumserweiterung, getestet

Gleiche Architektur, mehr Sleeves, Klassenbudget = 1/aktive Klassen, Span 2006-2026:

| Buch | CAGR | Sharpe | maxDD | Calmar | Brutto |
|---|---:|---:|---:|---:|---:|
| A) aktuelle 4, aktuelle Caps | 7.95% | **1.05** | **-11.62%** | **0.68** | 72.8% |
| B) aktuelle 4, gleiche Klassenbudgets | 8.37% | 0.95 | -13.53% | 0.62 | 88.1% |
| C) + EFA, TLT (6) | 8.63% | 0.94 | -14.25% | 0.61 | 90.9% |
| D) + EFA, EEM, TLT (7) | 7.40% | 0.79 | -15.12% | 0.49 | 92.3% |
| E) + EFA, EEM, TLT, DBC (8) | 7.21% | 0.73 | -17.24% | 0.42 | 94.9% |
| F) volles Universum (9, inkl. IWM) | 6.57% | 0.67 | -21.56% | 0.30 | 95.1% |

**Breite macht es monoton schlechter.** Gründe: EFA/EEM/IWM sind in Krisen 0.85-0.95 zu
SPY korreliert, der 60-Tage-Selektor filtert das nicht zuverlässig, und mehr Sleeves
erhöhen vor allem das Brutto (73% → 95%), nicht die Diversifikation. Das widerlegt die
Phase-2-Hypothese aus `universe-expansion-plan` in ihrer naiven Form.

### Ehrliche Einordnung

Diese Vergleiche laufen auf demselben 21-Jahres-Sample, auf dem die aktuellen Caps von
Hand gewählt wurden. „Aktuell ist am besten" ist also teilweise ein Selektionsartefakt.
Die belastbare Aussage lautet nicht „die Konfiguration ist optimal", sondern: **keine der
getesteten Alternativen zeigt einen Vorteil, der über das Rauschen hinausgeht**
(Bootstrap-90%-KI der Baseline: [0.71, 1.40]).

**Antwort auf „Rendite signifikant steigern": über die Strategie geht das derzeit nicht.**
Der gesamte verfügbare Gewinn liegt in Messung und Risikosteuerung.

---

## 4. Backtesting-Roadmap

1. **Golden-Test gegen den Ausführungsbug.** Ein Buch mit konstant w=1.0 in einem Asset
   muss exakt Buy&Hold reproduzieren (bis auf Kosten); w=0 muss exakt den Cash-Yield
   ergeben. Dieser eine Test hätte Befund 1 gefunden.
2. **Parity-Test live vs. Backtest auf Entscheidungsebene.** Dieselben historischen Bars
   durch `TradingSystem._compute_live_target_book()` und durch den `PortfolioBacktester`;
   die Gewichtsreihen müssen identisch sein. Der bestehende Target-Weight-Parity-Test
   deckt die Ausführung nicht ab.
3. **Kosten als Standardspalte.** Jede Tabelle bei 2/5/10/20 bps. Entscheidungsregel
   vorab fixieren: eine Änderung zählt nur, wenn sie bei 10 bps noch gewinnt.
4. **Monte-Carlo / Stress:**
   * Block-Bootstrap der Sharpe (existiert, 21-Tage-Blöcke)
   * Startdatum-Sensitivität (rollierende 5-Jahres-Fenster)
   * ausgefallene Entscheidungen (1-3 Bars übersprungen — Feiertage, Reboots, Timer-Fehler)
   * Slippage-Schock: 4× Kosten speziell an den 107 High-Turnover-Tagen
5. **Deflated Sharpe Ratio.** Über viele Sessions wurden dutzende Varianten getestet;
   die berichteten Sharpes müssen um die Zahl der Versuche korrigiert werden.
6. **Subperioden-Tabelle als Pflichtausgabe:**

   | Block | CAGR | Sharpe | maxDD |
   |---|---:|---:|---:|
   | 2005-2009 | 8.17% | 1.01 | -7.75% |
   | 2010-2014 | 7.30% | 1.01 | -10.43% |
   | 2015-2019 | 4.92% | **0.81** | -11.23% |
   | 2020-2026 | 10.87% | 1.29 | -11.62% |

   Das schwache Fenster ist 2015-2019 — Seitwärtsmarkt mit häufigen SMA-Durchläufen,
   also genau die Marktphase, in der ein Trendsystem strukturell leidet.
7. **Marktphasen-Test explizit:** Performance konditioniert auf (Trend an/aus) ×
   (Vol hoch/niedrig). Beantwortet die Seitwärtsmarkt-Frage direkt statt indirekt.
8. **Erst nach 1-3 wieder Strategie-Varianten testen**, weiterhin pre-registriert.

---

## 5. Reihenfolge

**P0 (Messung & Sicherheit, keine Strategieänderung):** Ausführungsbug + Golden-Test ·
Breaker-State persistieren + Lifecycle-Test · Pfad-Entscheidung, Schalter explizit ·
alle Reports auf den echten Live-Pfad neu erzeugen.

**P1:** Selektor-Hysterese · Daten-Sanity-Switch · echte Slippage aus dem Paper-Handel
messen (Phase 3) · toten Code entfernen (`size_position`, `max_risk_per_trade`, HMM).

**P2:** Erst dann — und nur pre-registriert — wieder über Rendite reden.

**Erwartungskorrektur:** realistisch sind **~8% CAGR / Sharpe ~1.0 / DD ~-12%** bei 73%
Brutto. Nicht +15.4%.

---

## Anhang — Messskripte

Im Session-Scratchpad (flüchtig), reproduzierbar:
`a1_overnight.py` (Befund 1), `a2_livepath.py` (Live-Pfad 3-fach + Diagnostik),
`a3_variants.py` (Variante A), `a4_robust.py` (Kosten/Subperioden/Krisen/Bootstrap),
`a5_riskcheck.py` (Befund 2), `a6_breadth.py` (Variante B).
Datenquelle: `scripts/run_experiments.fetch_yahoo`, 30y adjusted.

---

# NACHTRAG — Ziel geändert: den Index (SPY) schlagen

Auf Nachfrage getestet: kann das System den S&P 500 in der **absoluten Rendite**
schlagen? ~35 Varianten, alle mit realistischer Finanzierung (^IRX + Margin-Spread),
Reg-T-Deckel 2,0, 2 bps Kosten, IS/OOS-Split 2016.

## Die Ausgangsarithmetik

| | CAGR | Vol | Sharpe | maxDD |
|---|---:|---:|---:|---:|
| SPY buy&hold | 10,91% | 19,7% | 0,62 | −55,2% |
| heutiges Buch | 8,00% | **7,6%** | **1,06** | **−11,6%** |

Das Buch läuft auf **einem Drittel der Indexvolatilität**. Ein 7,6%-Vol-Portfolio kann
ein 19,7%-Vol-Asset nicht in der absoluten Rendite schlagen — das ist Arithmetik, nicht
Strategiequalität. Der Sharpe-Vorsprung (1,06 vs 0,62) ist real, ergibt bei
risikogematchtem Vergleich aber nur +1 bis +3pp CAGR, und die Finanzierung frisst davon
den Großteil.

Rollierende Trefferquote des heutigen Buchs gegen SPY: **9% der 5-Jahres-, 0% der
10-Jahres-Fenster.**

## Was NICHT funktioniert

* **Ungehebelte Varianten:** keine schlägt SPY zuverlässig. `equity_only` −0,69%,
  `momentum_all` −0,49%, `spy_trend` −2,73%. Einzige Ausnahme `qqq_trend` mit +0,69% —
  das ist aber eine Tech-Assetwette, kein Strategie-Edge.
* **Margin-Hebel:** `current x2.0` erreicht 12,05% (+0,91% vs SPY), Sharpe fällt 1,05 →
  0,83, DD −23%. Bei einem Margin-Spread von +5% über ^IRX schlägt es SPY **nicht mehr**.
* **Vol-Zielsteuerung:** `current` auf 20% Vol-Ziel = 11,08% vs SPY 11,14% — Gleichstand.
  Der Reg-T-Deckel bindet, das Ziel wird nie erreicht.
* **Das −20% HALT tötet jede indexschlagende Variante:**

  | Variante | ohne HALT | mit −20% HALT |
  |---|---:|---:|
  | current x2.0 | 12,05% | **2,78%** |
  | equity_only x1.0 | 10,44% | **2,05%** |
  | qqq_trend x1.0 | 11,82% | **1,50%** |

  Der sticky Halt feuert früh und gibt nie wieder frei. Indexschlagende Rendite und die
  heutige Risikokonfiguration schließen sich gegenseitig aus.

## Was funktioniert: trendgefilterte gehebelte ETFs

Kein Margin-Konto, kein Margin-Call, kein Reg-T — der Hebel steckt im Produkt
(Finanzierung und Vol-Decay sind in den echten Kursen enthalten). Signal auf dem
ungehebelten Basiswert, gehandelt wird der 2x/3x-ETF. Span 2007-2026:

| Variante | CAGR | vs SPY | Vol | Sharpe | maxDD | 10J-Quote |
|---|---:|---:|---:|---:|---:|---:|
| Trend(SPY) → SPY | 8,20% | −2,71% | 11,8% | 0,73 | −24,3% | 0% |
| Trend(SPY) → SSO (2x) | 12,47% | +1,56% | 23,6% | 0,62 | −43,3% | **79%** |
| Trend(SPY) → UPRO (3x) | 17,49% | +6,58% | 35,7% | 0,63 | −58,4% | **98%** |
| Trend(QQQ) → QLD (2x) | 19,87% | +8,96% | 31,6% | 0,73 | −46,8% | **94%** |
| Trend(QQQ) → TQQQ (3x) | 29,29% | +18,37% | 47,3% | 0,78 | −59,7% | n/a |

Der Trendfilter verhindert hier den Totalverlust, den ein gehaltener 3x-ETF durch 2008
erlitten hätte. Aber: der **Sharpe bleibt bei 0,62–0,78**, also auf Indexniveau. Das
sind keine besseren Strategien — es ist dieselbe Wette, größer gefahren.

## Entscheidungs-Menü (Mischung, ein Konto, Summe 100%)

| Mischung | CAGR | vs SPY | Vol | Sharpe | maxDD | 5J-Quote |
|---|---:|---:|---:|---:|---:|---:|
| 100% heutiges Buch | 8,00% | −2,91% | 7,6% | **1,06** | **−11,6%** | 9% |
| **75% Buch + 25% Trend-QLD** | **11,56%** | **+0,65%** | 13,0% | **0,91** | **−19,8%** | 32% |
| 60% Buch + 40% Trend-QLD | 13,52% | +2,61% | 16,6% | 0,85 | −24,5% | 58% |
| 50% Buch + 50% Trend-QLD | 14,75% | +3,84% | 19,0% | 0,82 | −28,7% | 60% |
| 50% Buch + 25% SSO + 25% QLD | 12,83% | +1,91% | 16,5% | 0,82 | −27,5% | 53% |
| 100% Trend-QLD | 19,87% | +8,96% | 31,6% | 0,73 | −46,8% | 85% |

**Empfehlung: 75% heutiges Buch + 25% trendgefiltertes QLD.** Das ist die einzige
Konfiguration, die den Index schlägt und dabei ein *deutlich* besseres Risikoprofil als
der Index behält: Sharpe 0,91 vs 0,62, Drawdown −19,8% vs −55,2%.

## Verhalten in den drei schlimmsten Phasen

| Phase | SPY | heutiges Buch | 50/50 SSO | 100% Trend-SSO |
|---|---:|---:|---:|---:|
| 2008-09 … 2009-03 | −36,9% | +2,7% | +1,5% | +0,2% |
| 2020-02 … 2020-04 | −13,5% | −5,7% | **−21,4%** | **−34,9%** |
| 2022-01 … 2022-10 | −17,7% | −5,0% | −11,4% | −17,6% |

Entscheidend: **gehebelter Trend funktioniert in langsamen Bärenmärkten (2008, 2022) und
versagt in schnellen Crashs (2020).** Der SMA-200 steigt zu spät aus; den Absturz frisst
man gehebelt mit.

## Ehrliche Einschränkungen

1. **Multiples Testen.** Ich habe ~35 Varianten gegen ein bekanntes Ergebnis getestet.
   Die rollierenden Trefferquoten mildern das, aber es bleibt *eine* 20-Jahres-Stichprobe
   mit einer bestimmten Abfolge (zwei langsame Bärenmärkte + ein außergewöhnlicher Bulle).
2. **QLD/TQQQ-Ergebnisse sind eine Tech-Wette.** Sie unterstellen, dass QQQ SPY weiter
   schlägt — 20 Jahre Momentum, kein Strategie-Edge.
3. **Tail-Risiko gehebelter ETFs ist nicht im Sample.** Ein einzelner −33%-Tag löscht
   einen 3x-Fonds aus, −50% einen 2x-Fonds. So etwas kam 2007-2026 nicht vor.
4. **Das −20% HALT müsste auf ~−35% angehoben oder abgeschafft werden.** Damit gibt man
   den heutigen Hauptschutz auf.
5. **Voraussetzung bleiben die P0-Fixes oben.** Ein System zu hebeln, dessen Backtester
   85% der Rendite verwirft und dessen Circuit Breaker im Betrieb nicht feuern, wäre der
   falsche Reihenfolge.

---

# UMSETZUNG — 60/40-Variante implementiert (Branch `levered-sleeve-60-40`)

Entscheidung des Owners: die 60/40-Mischung wird umgesetzt. Bewusst mitgekaufter
Trade-off: **+2,6pp CAGR gegen −0,18 Sharpe und den doppelten Drawdown.**

## Zielzahlen (Referenz, `scripts/sleeve_check.py`, 2007-04-10 … 2026-08-10)

| Buch | CAGR | Vol | Sharpe | maxDD |
|---|---:|---:|---:|---:|
| SPY buy&hold (Benchmark) | 11,09% | 19,7% | 0,63 | −55,19% |
| Kern allein (`core_scale` 1,0) | 7,89% | 7,6% | 1,04 | −11,75% |
| Sleeve allein Trend(QQQ)→QLD | 20,43% | 31,7% | 0,75 | −46,80% |
| **60% Kern + 40% Sleeve** | **13,66%** | 16,6% | **0,86** | **−24,81%** |

vs SPY **+2,57pp p.a.**; Trefferquote 55% (3J) / 57% (5J) / 56% (10J).

## Was geändert wurde

**1. Ausführungsbug behoben** (`core/portfolio_backtester.py`) — Voraussetzung
für alles Weitere, sonst ist die Variante nicht bewertbar. Die Bar-Rendite ist
jetzt `(1 + w[T-1]·Overnight) · (1 + w[T]·Intraday) − 1`. Die beiden Legs werden
**verkettet, nicht addiert**; der Kreuzterm war am 2025-04-08 (+3,5% über Nacht,
−4,9% intraday) allein 17 bp wert. Abgesichert durch einen Golden-Test: ein
dauerhaft voll investiertes Buch reproduziert Buy&Hold jetzt **exakt** (Abweichung
2,2e-16) — genau der Test, der den Fehler von Anfang an gefunden hätte.

**2. Sleeve-Architektur** (`core/sleeves.py`, neu). `compose_book()` bildet
`core_scale · Kernbuch + Σ Sleeves`. **Live-Pfad und Backtester rufen dieselbe
Funktion** — die Ursache von Befund 0 ist damit strukturell geschlossen: eine
Änderung wirkt auf beide oder auf keinen. QLD ist als `role: "sleeve"` markiert
und deshalb vom Allokator und vom Korrelationsselektor ausgenommen (der würde ihn
täglich verwerfen: höchste Vol, ~0,95 Korrelation — für einen Diversifikator
richtig, für eine bewusst budgetierte Beta-Position falsch).
Ein Sleeve wird **nie** bei undefiniertem Trend (`None`) eröffnet.

**3. HALT −20% → −35%** (`settings/config.py`). Gemessen, nicht geraten: das
60/40-Buch hat selbst −24,8% Rückgang, eine −20%-Bremse liegt also *innerhalb*
seines normalen Arbeitsbereichs und schaltet es dauerhaft ab (CAGR 13,66% → 1,00%,
Sharpe 0,86 → 0,18). −35% statt −25%, weil eine Bremse knapp hinter dem zufällig
im Sample liegenden Tiefpunkt an genau diesen einen Pfad angefittet wäre.

**4. Risiko-State wird persistiert** (`core/risk_manager.py`) — ohne das wäre die
neue Schwelle so wirkungslos wie die alte. `peak_equity` und die Tagesschluss-
Historie liegen jetzt in `logs/RISK_HALT_state.json` (atomar geschrieben) und
überleben den Prozessneustart. Test pinnt beide Zustände: **ohne** Persistenz
erreicht ein −45%-Crash über 12 Sessions `CBLevel.NONE`, **mit** Persistenz feuert
HALT. `clear_lock()` verankert den Peak neu, sonst könnte der Bot nach einem Halt
nie wieder starten.

**5. Ökonomische Hebelprüfung** (`RiskManager.validate_book`). Nominal ≠ Risiko:
0,40 in einem 2x-ETF sind 0,80 Marktexposure. Neuer `economic_gross_cap: 1.50`
über `Σ|w_i|·leverage_i`. Gemessen im Produktionslauf: Brutto nominal 75,8%
(max 100%), ökonomisch **108,1%** (max 140%).

**6. `portfolio_batch_loop` explizit in `config.BROKER`** — der Schalter existierte
nur als unsichtbarer `.get()`-Default.

**7. `main.py --backtest` rechnet jetzt mit echten Annahmen** (Kosten und
Cash-Verzinsung aus `config.BACKTEST` statt Konstruktor-Defaults von 0) und gibt
CAGR/Sharpe aus. Der Portfolio-Backtester akzeptiert jetzt auch die echte
^IRX-Zinsreihe — Parität mit dem Einzel-Asset-Backtester.

## Validierung

`python scripts/sleeve_check.py --validate` stellt den Produktionscode gegen eine
unabhängige Referenzimplementierung:

| | CAGR | Vol | Sharpe | maxDD |
|---|---:|---:|---:|---:|
| Referenz | 13,65% | 16,6% | 0,86 | −24,81% |
| Produktion | 13,67% | 16,6% | 0,86 | −24,76% |

**Zielgewichte identisch** (max. Abweichung 4e-07 = die 6-Stellen-Rundung, über
4865 Bars × 5 Assets). Die Restdifferenz in CAGR/DD ist die Intra-Bar-Verkettung,
die die Referenz bewusst nicht macht — die Produktion ist hier die korrektere.

Produktionsbuch: SPY 22,0% · QLD 32,4% · IEF 10,1% · GLD 8,0% · QQQ 3,3%;
Turnover 9,5×/Jahr.

## Was weiterhin offen bleibt

* **Befund 6** (Live/Backtest-Timing): der Timer entscheidet 09:35 auf einem noch
  unfertigen Tagesbalken, der Backtest auf dem fertigen Close. Unverändert.
* **Befund 5** (`max_risk_per_trade`/`size_position` toter Code): unverändert.
* **Befund 7** (Klassen-Cap-Bug in `run_once`): unverändert — betrifft nur den
  Nicht-Default-Pfad.
* **Phase 3**: echte Slippage aus dem Paper-Handel messen. Bei 10 bps fällt der
  Sharpe des Kernbuchs von 1,06 auf 0,93; die Kostenannahme von 2 bps ist die
  wichtigste ungeprüfte Zahl.
* **Der Tail-Risiko-Vorbehalt gilt unverändert**: ein einzelner −50%-Tag löscht
  einen 2x-ETF aus. So etwas kam 2007–2026 nicht vor. Und gehebelter Trend
  versagt in schnellen Crashs (2020-02..04: 60/40 −15,8% vs SPY −13,5%).

# NACHTRAG 2026-08-12 — Befund 5, 6, 7 geschlossen

Die drei zuvor zurückgestellten Code-Befunde wurden jetzt behoben (Branch
`levered-sleeve-60-40`, im Anschluss an die 60/40-Umsetzung oben):

* **Befund 6** (Timing-Divergenz): `MarketDataFeed.get_latest_bar` verwirft jetzt
  standardmäßig einen Tagesbalken, dessen Session noch läuft (America/New_York,
  16:00-Schluss). Der 09:35-Lauf entscheidet damit auf dem letzten VOLLSTÄNDIGEN
  Close — exakt das Backtester-Modell ("Entscheidung auf Close T-1, Fill bei
  Open T"). Die Staleness-Prüfung ist jetzt pro Prozess (`last_decision_ts`),
  nicht mehr "ist die Historie gewachsen" — Letzteres hätte den täglichen
  `--once`-Lauf für immer als „stale" markiert, sobald er korrekt auf dem
  fertigen Balken entscheidet, den sein eigener Startup-Fetch bereits geladen
  hat. Sizing/`expected_price` nutzen jetzt den aktuellen Kurs
  (`get_latest_price`), nicht den Entscheidungs-Close, sonst wäre die Positionsgröße
  um genau die Overnight-Bewegung falsch gewesen.
* **Befund 5** (toter Code): `size_position()`/`max_risk_per_trade` entfernt.
  Kein Pfad rief die Methode je auf; die Konfiguration versprach eine Regel, die
  es nicht gab. Ein Nachbau hätte einen `stop_price` gebraucht, den es im Live-Pfad
  nicht gibt (Stops sind aus guten, gemessenen Gründen deaktiviert) — Löschen war
  die ehrliche Lösung, nicht Nachrüsten.
* **Befund 7** (Klassen-Cap-Bug in `run_once`): `RiskManager.clip_target_weight()`
  ersetzt den Alles-oder-Nichts-Reject durch ein Clipping auf das verbleibende
  Budget. Vorher gewann der zuerst entschiedene Ticker eines Asset-Class-Budgets
  dauerhaft; ein zweiter Ticker derselben Klasse wurde auf 0 gesetzt, selbst wenn
  eine kleinere Position gepasst hätte. Betrifft weiterhin nur den
  Nicht-Default-Pfad (`portfolio_batch_loop=True` bleibt der produktive Pfad).

Weiterhin offen: Phase 3 (echte Slippage aus Paper-Handel) und der
Tail-Risiko-Vorbehalt oben — beide unverändert.

# NACHTRAG 2026-08-13 — Vol-Gate für den Sleeve geprüft und verworfen

Reaktion auf den Tail-Risiko-Vorbehalt oben ("gehebelter Trend versagt in
schnellen Crashs"): eine frühere, UNREGISTRIERTE Exploration hatte ein
Realized-Vol-Gate (QLD auf 0 sobald QQQs kurzfristige Vol gegenüber ihrem
eigenen Median explodiert, statt auf den SMA-200-Ausstieg zu warten) gegen
genau zwei bekannte Krisen (2020, 2022) gescort und sah dabei stark aus —
klassische Overfitting-Falle, siehe [[universe-expansion-plan]] /
Do-not-tune-Liste. Deshalb vor Implementierung ein vorregistriertes Protokoll
mit Owner-Freigabe (2026-08-13):

* **Regel (fix, keine erneute Grid-Suche)**: QLD-Gewicht auf 0, wenn QQQs
  10-Tage-Realized-Vol das 2,0-fache ihres eigenen 252-Tage-Medians
  überschreitet.
* **Bestehens-Kriterium**: Übernahme nur wenn, auf echten Daten (Blended-Buch
  2007-04-10..2026-08-10, Fenster durch GLD-Historie begrenzt) Sharpe nicht
  um mehr als 0,02 fällt, CAGR nicht um mehr als 0,5pp fällt, UND maxDD in
  JEDER von vier Krisen (Dotcom, GFC, 2020, 2022) gleich oder besser ist.
  Dotcom (2000-02, vor QLDs echtem Start 2006-06-21) nur Sleeve-isoliert,
  mit einem synthetischen täglich neu gehebelten 2x-QQQ-Proxy — GLD/IEF
  existieren vor 2004/2002 ebenfalls nicht, ein Blended-Buch ist dort ohnehin
  nicht berechenbar.

**Ergebnis** (`scripts/sleeve_check.py`-Referenzmethodik ergänzt um das Gate,
Skript: `scripts/vol_gate_check.py`):

| Fenster | Baseline maxDD | Gated maxDD | Ergebnis |
|---|---:|---:|---|
| Dotcom (Sleeve-only, synthetisch) | -74.91% | -74.02% | OK |
| GFC (2007-10..2009-03) | -17.10% | **-18.62%** | **FAIL** |
| 2020-02..04 | -24.50% | -13.54% | OK |
| 2022 | -18.19% | -16.12% | OK |

Sharpe/CAGR-Bar auf dem Gesamtzeitraum bestanden (Sharpe +0,08, CAGR +0,45pp),
aber die GFC-Krise — nie Teil der ursprünglichen Auswahl — schneidet mit Gate
SCHLECHTER ab als ohne. Plausible Erklärung: 2008 war ein zäher,
mehrphasiger Bärenmarkt mit wiederholten Vol-Spikes und -Beruhigungen statt
eines einzelnen scharfen Crashs wie 2020 — das Gate flackert dort rein/raus
und verpasst Erholungsphasen, die der reine SMA-200-Ausstieg mitgenommen
hätte.

**Verdikt: REJECT — Vol-Gate wird nicht implementiert.** Genau der Fall, für
den die Vorregistrierung gedacht war: auf den zwei Krisen, gegen die informell
gesucht wurde, ein klarer Gewinn; auf der dritten, ungesehenen Krise ein
klarer Verlust. Damit ist diese Idee jetzt eine abgeschlossene, getestete
Entscheidung — nicht mehr offene "schwache Evidenz". Der Tail-Risiko-Vorbehalt
(schnelle Crashs) bleibt unverändert bestehen; ein Vol-Gate ist nicht die
Lösung dafür.

---

## Nachtrag 2026-08-24 — Reproduzierbarkeit der 13,66%-Zahl (Befund H3)

Der Vollaudit vom 2026-08-24 hat bemängelt, dass für dasselbe Buch mehrere
CAGR-Zahlen im Repo kursieren (hier 13,66%, `analysis_report_2026-08-13_rendite.md`
12,95%) ohne festgeschriebenes Reproduktionsskript. Beide Zahlen sind korrekt —
sie messen unterschiedliche Zeitfenster, wie der 08-13-Report selbst bereits
dokumentiert hatte. Die 13,66% oben (Span 2007-04-10 … 2026-08-10, per
`scripts/sleeve_check.py`) sind die **kanonische** Zahl: der natürliche Beginn
des gemeinsamen Datenindex nach dem 200-Bar-SMA-Warmup, ohne künstlichen
`start_date`-Clip. `scripts/reproduce_headline.py` (neu) reproduziert sie jetzt
direkt aus `core/portfolio_backtester.py` mit Commit-Hash, Datenstand-Hash,
Span und Kostenzeile im Output: aktuell 13,67% CAGR / Sharpe 0,86 / maxDD
−24,76% (die 1bp CAGR-Differenz zur hier dokumentierten Zahl liegt innerhalb
normaler Datenrevision seit 2026-08-01, keine Regressionsursache).
