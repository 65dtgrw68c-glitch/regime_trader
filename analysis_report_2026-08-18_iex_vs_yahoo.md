# H2 — IEX- vs. Yahoo-Close-Divergenz — 2026-08-18

Auftrag (aus dem Security-Review 2026-08-17, Befund H2): messen, wie weit die
live genutzten IEX-Closes (`data/market_data.py`, `feed=DataFeed.IEX`) von den
Yahoo-adjusted-Closes abweichen, die jede Validierung/jeder Backtest nutzt
(`scripts/sleeve_check.py`, `core/portfolio_backtester.py` über die
Alpaca-IEX- bzw. Yahoo-Quelle in `scripts/run_experiments.py`) — bevor über
irgendeine Änderung entschieden wird. Reines Messproblem, keine
Strategieänderung; nichts am Produktionscode wurde angefasst.

**Messskript:** `scripts/iex_vs_yahoo_check.py` (neu, nutzt die bereits
vorhandenen `fetch_alpaca()`/`fetch_yahoo()`-Funktionen aus
`scripts/run_experiments.py` — beide holen bereits split+dividend-adjustierte
Daten, genau wie der Live-Pfad). Für jeden Ticker: beide Quellen laden, auf
den Kalendertag normalisieren (IEX trägt `04:00:00 UTC` als Bar-Timestamp,
Yahoo bereits Mitternacht-normalisiert — vor der Normalisierung war der exakte
Timestamp-Join leer, 0 überlappende Tage), Differenz in bps, und unabhängig
`core.regime_strategies.is_trend_confirmed`s exakte Regel
(`close > SMA200`, strikt kausal) auf beiden Quellen nachgebaut, um zu sehen,
wie oft das tatsächliche Handelssignal – nicht nur der Rohpreis – abweicht.

**Zeitraum:** IEX-Historie dieses Accounts reicht überraschend weit zurück —
SPY ab 2018-11-01, QQQ/GLD/IEF/QLD ab 2020-07-27, jeweils bis 2026-08-18
(~5.4–7.8 Jahre, deutlich mehr als die im Code dokumentierte Annahme "~seit
2020"). Deckt COVID-Crash 2020 und den 2022er Bärenmarkt ab, aber NICHT die
25-30-Jahre-Strukturvalidierung (2000, 2008), die an anderer Stelle in diesem
Projekt verwendet wird — vor 2018 existiert keine IEX-Historie zum Vergleich.

## Ergebnis

| Ticker | Tage (Zeitraum)            | ØAbw. (bp) | Median (bp) | Max (bp, Datum)        | Tage >5bp | Tage >20bp | SMA200-Flag-Widersprüche |
|--------|-----------------------------|-----------:|------------:|-------------------------|----------:|-----------:|---------------------------|
| SPY    | 1523 (2018-11-01…2026-08-18)| 1.61       | 1.07         | 103.67 (2025-04-09)     | 2.5%      | 0.2%       | **0 / 1324**               |
| QQQ    | 1522 (2020-07-27…2026-08-18)| 7.96       | 2.21         | 96.26 (2024-12-23)      | 38.2%     | 11.7%      | **0 / 1323**               |
| GLD    | 1522                         | 1.20       | 0.81         | 30.62 (2021-06-18)      | 2.1%      | 0.1%       | 2 / 1323                   |
| IEF    | 1522                         | 1.22       | 0.91         | 25.91 (2024-12-23)      | 1.5%      | 0.1%       | 3 / 1323                   |
| QLD    | 1522                         | 19.66      | 13.90        | 206.99 (2021-05-18)     | 72.1%     | 41.4%      | 2 / 1323 (nicht signalrelevant, siehe unten) |

### 1. Die beiden entscheidungsrelevanten Ticker widersprechen sich in ~5.4–7.8 Jahren NIE

SPY (Core-Signal) und QQQ (Core-Signal UND alleiniger Trigger des
QLD-Sleeves, `SLEEVES["levered"][0]["signal"] = "QQQ"`) zeigen **0
SMA200-Flag-Widersprüche** über 1324 bzw. 1323 vergleichbare Tage — obwohl
QQQs rohe Preisabweichung deutlich größer ist als SPYs (Median 2.2bp vs.
1.1bp, Ø 8.0bp vs. 1.6bp, 38% der Tage >5bp vs. 2.5%). Rohes Preisrauschen
zwischen den Feeds überträgt sich in dieser Stichprobe nicht in
Entscheidungsrauschen — für genau die zwei Ticker, deren eigener Close
tatsächlich eine Live-Order auslöst.

### 2. GLD/IEF: seltene Widersprüche, aber jeder einzelne ist ein Rasierklingen-Tag

2 von 1323 (GLD) bzw. 3 von 1323 (IEF) Tagen — 0.15–0.23%. Auf JEDEM dieser
Tage lag der Close bei BEIDEN Quellen innerhalb von ~2bp der eigenen SMA200:

- GLD 2022-01-10: IEX −1.48bp, Yahoo +0.41bp
- GLD 2022-02-02: IEX −1.22bp, Yahoo +1.79bp
- IEF 2021-11-08: IEX −0.19bp, Yahoo +0.92bp
- IEF 2023-12-01: IEX −0.45bp, Yahoo +1.71bp
- IEF 2025-02-07: IEX +0.73bp, Yahoo −0.56bp

Das ist exakt das Muster, das zwei unabhängige, an sich unauffällige
Preisquellen an einer Schwelle erzeugen — kein Hinweis auf einen
systematisch verzerrten IEX-Feed.

### 3. QLD: größere Rohabweichung, aber signalirrelevant — die Sleeve-Steuerung nutzt QQQs Close, nicht QLDs eigenen

QLD zeigt die mit Abstand größte Divergenz (Median 13.9bp, Ø 19.7bp, 41.4%
der Tage >20bp) — erwartbar für ein weniger liquides 2x-Produkt auf IEX.
Das ist aber **kein Trendsignal-Problem**: `compose_book()` gated den
QLD-Sleeve ausschließlich über QQQs eigenen Trend (bewusst so gebaut, weil
QLDs eigene SMA durch den täglichen Reset verzerrt ist — Kommentar in
`settings/config.py`). QLDs eigener SMA200-Flag wird in Produktion nie
konsultiert.

Die einzige bemerkenswerte Einzelbeobachtung der ganzen Messung: am
2023-03-15 lag QLDs Close bei Yahoo +60.4bp über der eigenen SMA200, bei IEX
nur −9.6bp darunter — kein Rasierklingen-Tag, eine echte 70bp-Diskrepanz.
Relevant ist das für die POSITIONSGRÖSSE (wie viele QLD-Anteile ein
Rebalance kauft), nicht für das Signal — schließt an die noch offene
Phase-3-Slippage-Messung an (`scripts/slippage_check.py`, bislang 0 echte
Fills, siehe Projekt-Memory).

## Fazit

Die im Review formulierte Sorge — ein systematisch anderer Close könnte das
SMA200-Trendsignal verfälschen — bestätigt sich empirisch NICHT für die
Ticker, deren eigener Close tatsächlich eine Entscheidung auslöst (SPY,
QQQ direkt; GLD/IEF mit vernachlässigbarer, plausibel erklärter
Restrate). Die Abweichung selbst ist real und messbar (am größten bei
QQQ/QLD, beides auf IEX weniger liquide Namen), kippt aber in dieser
Stichprobe praktisch nie das Bit, auf dem die Strategie handelt.

**Owner-Entscheidung:** Auf Basis dieser Zahlen lässt sich ein Wechsel auf
den kostenpflichtigen SIP-Konsolidiert-Feed (Alpaca "Algo Trader Plus",
Preis hier nicht verifiziert) allein mit Signal-Integrität kaum begründen.
Die durch diese Messung eher aufgeworfene Frage ist QLDs Preisqualität für
die POSITIONSGRÖSSE, nicht für das Signal — das gehört zur ohnehin noch
offenen Slippage-Messung (Phase 3), nicht zu einer neuen Baustelle.

**Caveats:**
- Nur IEX-verfügbare Historie dieses Accounts (~5.4–7.8 Jahre) — keine
  25-30-Jahre-Strukturvalidierung, 2008 unmessbar (keine IEX-Daten davor).
- Gemessen wird der Tagesschluss, nicht die Ausführungsqualität: der Bot
  sized/exekutiert über `get_latest_price()` (aktueller Quote), nicht über
  den hier verglichenen historischen Close — eine separate Frage.
- Vergleich ist IEX vs. Yahoo, nicht IEX vs. SIP (die tatsächliche
  konsolidierte Tape); Yahoo dient hier als plausibler, aber nicht
  garantiert identischer Proxy für "nicht-IEX".

Nichts am Produktionscode geändert; Skript `scripts/iex_vs_yahoo_check.py`
committed, Report-Datei nicht committed.
