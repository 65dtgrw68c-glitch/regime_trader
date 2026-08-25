# Renditeanalyse regime_trader — 2026-08-13

Auftrag: maximale CAGR / Profit Factor, ohne das System durch Curve-Fitting zu zerstören.
Harte Restriktionen (Owner-Konsens): keine Grid-Searches, SMA-200 / Vol-Targets / Breaker-
Schwellen unangetastet, 60/40 fixiert, Universum fixiert, HMM bleibt inert.

Alle Zahlen unten sind **in dieser Session gegen den Produktionscode gemessen**
(`core/portfolio_backtester.compute_daily_targets` bzw. eine bit-treue Reimplementierung),
Yahoo adjusted, 2007-01-03 … 2026-08-10, 4731 Bars nach SMA-Warmup, Next-Open, 2 bps,
^IRX-Cash. Replikationskontrolle: maxDD -24.76% gegen dokumentierte -24.8% (`config.py`
SLEEVES-Kommentar), CAGR 12.95% gegen 13.66% — Differenz erklärt sich durch den
Spanbeginn (hier 2007-01-03 statt 2007-04-10). Das Buch ist korrekt nachgebildet.

Messskripte: Scratchpad `diag_capital.py`, `variants.py`, `variants2.py`, `verify.py`,
`recovery.py`.

---

## 0. Zwei Prämissen des Auftrags treffen auf den laufenden Code nicht zu

Beide gehören zur Fehlerklasse „mehrere verschiedene Systeme", die dieses Projekt am
2026-07-10 und erneut am 2026-08-01 (Befund 0) getroffen hat: ein Parameter gilt als
aktiv, für den Evidenz existiert — und ist im deployten Pfad wirkungslos.

### P1 — `confirm_bars=3` ist am Sleeve NICHT aktiv

Der deployte Pfad (`portfolio_batch_loop=True`) ruft `is_trend_confirmed()`
([core/regime_strategies.py:176](core/regime_strategies.py#L176)) auf. Diese Funktion hat
**keinen `confirm_bars`-Parameter** — sie ist ein blankes `close > SMA200`. Der
Bestätigungszähler lebt ausschließlich in `RegimeOrchestrator`
([core/regime_strategies.py:449](core/regime_strategies.py#L449)), und der wird im
Portfolio-Pfad nie instanziiert. `scripts/sleeve_check.py` rechnet ebenfalls mit
`close > close.rolling(200).mean()` — Referenzskript und Produktion sind konsistent,
beide **ohne** Bestätigung.

Gemessene Konsequenz: 118 Sleeve-Flips in 19.6 Jahren (6.0/Jahr). Von 60 ON-Episoden
sind **27 höchstens 3 Tage lang**, von 59 OFF-Episoden 28. Fast die Hälfte aller
Sleeve-Ein-/Ausstiege ist SMA-Hover-Geflacker auf einem 2x-Produkt mit 0.40 Notional
(= 0.80 gehandeltes Notional je Flip).

### P2 — Vol-Targeting 15% ist im deployten Pfad NICHT aktiv

`ORCHESTRATOR["vol_target"] = 0.15` wird ebenfalls nur von `RegimeOrchestrator`
gelesen. Im Portfolio-Pfad existiert **kein Portfolio-Vol-Target**. Die einzige
Verwendung von Volatilität ist die Inverse-Vol-Gewichtung innerhalb der Klassenbudgets
in [core/allocator.py:25](core/allocator.py#L25) — und die ist, siehe Abschnitt 2,
auf 96.3% der Tage ein No-Op.

> Das ist keine Empfehlung, beides einzuschalten (Messung dazu in Abschnitt 2 und 4).
> Es ist die Feststellung, dass die Systembeschreibung im Auftrag von dem abweicht,
> was um 09:35 NY tatsächlich rechnet.

---

## 1. Zwei echte Defekte

### Befund R1 — `validate_book` verwirft wegen Rundung das **ganze** Buch (Bug, kritisch)

`compose_book()` skaliert auf den Brutto-Cap und rundet **danach** jedes Gewicht auf
6 Dezimalen ([core/sleeves.py:104](core/sleeves.py#L104)). `validate_book()` toleriert
`1e-9` ([core/risk_manager.py:346](core/risk_manager.py#L346)). Vier Rundungsfehler à
bis zu 5e-7 sprengen diese Toleranz.

Minimalreproduktion (echter Fall vom 2017-06-09, Produktionscode):

```
Buch  {SPY: 0.214155, QQQ: 0.151063, GLD: 0.104348, IEF: 0.130435, QLD: 0.4}
gross 1.0000010000000001   → Überschuss 1.0e-06  →  approved=False
      "gross 1.00 exceeds cap 1.00"
```

Auf dem deployten Pfad ist das **kein per-Name-Clip, sondern ein Komplettausfall**:
[main.py:431-437](main.py#L431-L437) markiert bei `approved=False` *jeden* Ticker als
`rejected_by_risk` und kehrt zurück, bevor irgendein Auftrag entsteht. An dem Tag wird
gar nicht gehandelt.

Gemessen: **13 von 4731 Tagen (0.27%)**. Und zwar nicht zufällig verteilt — der Fehler
kann per Konstruktion nur auftreten, wenn das Buch exakt am Brutto-Cap liegt, also wenn
der Kern gesättigt **und** der Sleeve an ist. Es trifft systematisch die Tage mit der
höchsten Zielallokation.

Der Fix ist parameterfrei (Toleranz an die Rundung angleichen oder in `compose_book`
nach unten runden) und berührt keine Restriktion. Das ist die einzige Maßnahme in
diesem Report, die reine Gewinnrückgewinnung ohne Gegenleistung ist.

**Nebenbefund gleicher Klasse:** die Befund-7-Reparatur von 2026-08-12
(`clip_target_weight` statt Komplettablehnung) wurde nur im **nicht** deployten
Einzelasset-Pfad `run_once` eingebaut. Der deployte Portfolio-Pfad lehnt weiterhin
das ganze Buch ab.

### Befund R2 — 5.8% des Buchs sind totes Kapital, und es lässt sich nicht umschichten

Der Korrelationsselektor verwirft an **95.1%** der Tage, an denen SPY und QQQ beide im
Trend sind, einen der beiden (|ρ| > 0.80). Die Equity-Klasse hat dann genau einen Namen,
der das Klassenbudget 0.70 beanspruchen müsste — und wird von `per_name_cap = 0.50`
gekappt ([core/allocator.py:33](core/allocator.py#L33)). Die Differenz wird **nicht**
umverteilt, sie fällt ins Cash.

Gemessen: die Kappung bindet an **79.1%** der Tage, im Mittel 0.0971 Kern-Einheiten
= **0.058 Bucheinheiten** (nach `core_scale` 0.60).

Die naheliegende Reparatur — Spillover in andere Klassen — ist ein **mathematisches
No-Op**. Gemessener Restspielraum, wenn eine Klasse aktiv ist:

| Klasse | Cap | mittlerer freier Spielraum |
|---|---|---|
| equity | 0.70 | **0.1937** |
| gold | 0.20 | 0.0004 |
| bonds | 0.25 | 0.0005 |

Gold und Bonds sitzen immer exakt auf ihrem Klassencap. Es gibt keinen Empfänger. Die
implementierte Spillover-Variante kam bit-identisch zur Baseline heraus (Variante C in
Abschnitt 2). Das gestrandete Kapital ist nur durch **Anheben eines Caps** aktivierbar —
und was das kostet, steht im nächsten Abschnitt.

---

## 2. Bereich 1 — Allokations-Mathematik: der Hebel existiert nicht

### Der Allokator ist auf 96.3% der Tage wirkungslos

Die entscheidende Messung dieses Reports:

```
Tage, an denen IRGENDEINE Klasse ≥2 selektierte Namen hat:  175 / 4731  =  3.70%

  equity : 3908 aktive Tage, Ø 1.0448 Mitglieder, ≥2 an  4.48% der Tage
  gold   : 3123 aktive Tage, Ø 1.0000 Mitglieder, ≥2 an  0.00% der Tage
  bonds  : 3166 aktive Tage, Ø 1.0000 Mitglieder, ≥2 an  0.00% der Tage
```

Gold und Bonds haben **per Universumskonstruktion** genau einen Kandidaten (GLD, IEF).
Equity hat zwei, aber der Selektor entfernt einen davon fast immer. Eine
Gewichtungsformel *innerhalb* einer Klasse kann nur arbeiten, wenn die Klasse mindestens
zwei Mitglieder hat.

**Damit ist der gesamte Auftragsteck „Momentum-Velocity-Gewichtung statt Vol-Parität,
fraktionales Kelly innerhalb der Gross-Caps" mathematisch gegenstandslos.** Inverse-Vol,
Kelly, Risk Parity, HRP, Momentum-Velocity — alle liefern auf einer einelementigen Menge
dasselbe Ergebnis: das volle Klassenbudget an den einen Namen. Ein Wechsel der
Allokationsmathematik könnte höchstens 3.7% der Tage und dort nur die Equity-Klasse
beeinflussen.

Was das Buch tatsächlich ist: **ein trendgetorter Festgewichts-Korb.**
`0.60 × (SPY 0.50 | GLD 0.20 | IEF 0.25, je nach Trend) + 0.40 QLD, wenn QQQ im Trend.`

### Kelly bindet nicht, weil die Klassen-Caps zuerst binden

Gemessene ökonomische Exposure (Σ |w| × leverage) gegen `economic_gross_cap = 1.50`:

| | |
|---|---|
| Mittel | 1.074 |
| Median | 1.250 |
| p95 | 1.370 |
| Maximum | **1.400** |
| Tage ≥ 1.45 | **0.00%** |
| mittlerer ungenutzter Spielraum | 0.426 |

Der ökonomische Cap wird **nie** erreicht. Ein Kelly-Kriterium, das mehr Hebel
verlangt, würde also nicht am Risikolimit scheitern, sondern an den Klassen-Caps —
und die sind das, was man ändern müsste. Kelly wäre eine Umparametrisierung einer
Nebenbedingung, die gar nicht aktiv ist.

### Was passiert, wenn man das gestrandete Kapital doch aktiviert (gemessen)

Vier vorab festgelegte Varianten, kein Sweep:

| Variante | CAGR | Vol | Sharpe | maxDD | PF | Calmar | Brutto Ø |
|---|---|---|---|---|---|---|---|
| **A Baseline (deployed)** | 12.95% | 16.57% | **0.818** | **-24.76%** | 1.166 | **0.523** | 0.753 |
| F Selektor AUS | 13.61% | 17.95% | 0.801 | -27.06% | 1.162 | 0.503 | 0.807 |
| G `per_name_cap` 0.50→0.70 | 13.38% | 17.67% | 0.800 | -26.92% | 1.162 | 0.497 | 0.811 |
| H beide (F+G) | 13.59% | 18.17% | 0.793 | -27.68% | 1.161 | 0.491 | 0.811 |

Krisenfenster (Gesamtrendite im Fenster):

| | A | F | G | H |
|---|---|---|---|---|
| GFC 2007-11…2009-03 | **-14.77%** | -16.22% | -16.06% | -16.45% |
| Crash 2020-02…04 | **-9.84%** | -10.89% | -10.73% | -10.63% |
| Bär 2022 | **-16.86%** | -19.12% | -18.29% | -19.42% |

Jede Variante kauft 43–66 bp CAGR mit 2–3 pp mehr Drawdown, schlechterem Sharpe,
schlechterem Profit Factor und **monoton fallendem Calmar**. In allen drei Krisen ist
die Baseline die beste. Das ist kein Alpha, das ist zugekauftes Beta — und zwar
dasselbe Beta, in dem das Buch über den Sleeve ohnehin schon steckt. Deckt sich mit
dem Befund vom 2026-08-01 („proportional leverage — Calmar fällt monoton").

### Zusatzmessung: Sleeve-Bestätigung (P1) nachträglich einschalten

| Variante | CAGR | Sharpe | maxDD | Calmar |
|---|---|---|---|---|
| A Baseline | 12.95% | 0.818 | **-24.76%** | **0.523** |
| B Sleeve mit confirm3 | 13.25% | 0.828 | -27.93% | 0.475 |
| E confirm3 überall | 13.47% | 0.829 | -29.94% | 0.450 |

| Krisenfenster | A | B | E |
|---|---|---|---|
| GFC | **-14.77%** | -16.03% | -15.50% |
| 2020 | **-9.84%** | -15.56% | -18.17% |
| 2022 | -16.86% | -13.40% | **-13.70%** |
| 2018 Q4 | -17.86% | -11.17% | **-9.42%** |

Exakt das Muster, an dem das Vol-Gate am 2026-08-13 gescheitert ist: gewinnt in zwei
Krisen, verliert in zwei anderen, und der Drawdown wird über die volle Periode um
3.2 pp schlechter. **Gegen die etablierte Pre-Registration-Latte („maxDD überall
gleich oder besser") fällt das durch.** Die drei Tage Verzögerung, die im Bärenmarkt
Whipsaw dämpfen, sind im schnellen Crash drei Tage länger 2x-Hebel.

Der Punkt aus Abschnitt 0 bleibt trotzdem bestehen: der Owner sollte wissen, dass der
Parameter nicht wirkt — die Entscheidung, ihn *nicht* einzuschalten, ist danach eine
bewusste.

---

## 3. Bereich 2 — Execution: der Effekt ist real, aber zwei Größenordnungen kleiner als vermutet

### Wo die Rendite tatsächlich anfällt (2007–2026, annualisiert)

| | gesamt | Overnight (close→open) | Intraday (open→close) |
|---|---|---|---|
| SPY | +11.08% | **+7.74%** | +3.06% |
| QQQ | +16.39% | **+11.59%** | +4.27% |
| GLD | +9.94% | **+10.27%** | **-0.41%** |
| IEF | +3.21% | +1.17% | +2.01% |
| **QLD** | **+24.95%** | **+19.88%** | +4.13% |

Rund 80% der Sleeve-Rendite fällt zwischen Schluss und nächster Eröffnung an. Bei Gold
ist der Intraday-Beitrag sogar negativ. Die Intuition im Auftrag ist also richtig: wer
erst am nächsten Open einsteigt, verpasst bei jedem **neuen** Einstieg eine
Overnight-Session.

### Die ehrliche Größe des Effekts

Mechanische Rechnung: ~3.0 Sleeve-Einstiege pro Jahr × 0.40 Gewicht × 8.59 bp
(QLD-Overnight pro Session) = **~10 bp p.a.** aus dem Sleeve.

Der naive Full-Period-Backtest sagt mehr — und der ist nicht belastbar:

| Span | Next-Open | Same-Close | Δ CAGR |
|---|---|---|---|
| 2007–2011 | 1.99% / Sh 0.20 / DD -23.6% | 0.07% / Sh 0.09 / DD -25.6% | **-1.92%** |
| 2012–2016 | 10.76% / Sh 0.80 / DD -24.8% | 13.50% / Sh 1.00 / DD -16.3% | **+2.74%** |
| 2017–2021 | 22.90% / Sh 1.18 / DD -24.5% | 23.43% / Sh 1.23 / DD -24.0% | +0.53% |
| 2022–2026 | 15.57% / Sh 0.98 / DD -20.8% | 15.60% / Sh 0.98 / DD -20.9% | +0.03% |
| **VOLL** | 12.95% / Sh 0.82 / DD **-24.76%** | 13.34% / Sh 0.85 / DD -25.64% | +0.39% |

Die +39 bp der Gesamtperiode stammen praktisch vollständig aus **einem** Span
(2012–2016, dort auch die auffälligen 8.5 pp Drawdown-Differenz — ein einzelnes
Gap-Ereignis, nicht ein wiederkehrender Vorteil). In der Krisenperiode 2007–2011 kostet
dieselbe Umstellung 1.92 pp p.a. Über die volle Periode ist der maxDD **schlechter**.

Das ist kein Alpha, sondern die Frage, welches Buch am Rebalancing-Tag dem
Overnight-Gap ausgesetzt ist — ein zweiseitiges Risiko mit einem kleinen Drift-Edge
darauf. Belastbar ist der mechanische Teil (~10 bp p.a.), nicht die 39 bp.

### Warum Limit-Order-Fading auf diesem System negativen Erwartungswert hat

Entscheidende Messung:

```
Gesamtrendite 2007-2026                          883.3%
  ohne die 10 besten Tage                        536.8%
  ohne die 25 besten Tage                        295.1%
  ohne die 50 besten Tage                         99.4%
```

50 von 4731 Tagen tragen 89% der Gesamtrendite. Diese Tage sind überwiegend
Erholungs-Gap-Tage mit aktivem 2x-Sleeve — also **genau die Tage, an denen ein
passives Limit unter dem Eröffnungspreis nicht ausgeführt wird.**

Bei einem Trendsystem ist Nicht-Ausführung die teure Variante, nicht der Spread. Ein
Fade von 5–10 bp gegen die Chance, an einem +5%-Gap-Tag draußen zu stehen, ist ein
Tausch mit stark negativer Asymmetrie. „Liquidity Providing im Spread" ist bei 4–6
Aufträgen pro Monat in SPY/QQQ/GLD/IEF/QLD ohnehin keine erschließbare Ertragsquelle —
dafür braucht es Queue-Position und Tick-Daten, nicht einen Tagesbalken-Bot.

### Was hier stattdessen ansteht

Die 2 bps sind bis heute **unverifiziert**. `scripts/slippage_check.py` existiert seit
`633f287` und braucht 30 Fills; laut Memory-Stand hat der Server seit dem 12.08. seinen
ersten Lauf auf dem neuen Code noch vor sich. Bei 10 bps statt 2 fällt der Kern-Sharpe
1.06 → 0.93 (gemessen 2026-08-01). **Erst messen, dann über Ausführungslogik reden.**

---

## 4. Bereich 3 — Recovery: der Engpass ist nicht der Sleeve

### Gemessene Wiedereinstiegs-Verzögerung nach den Tiefpunkten

| Episode | Tief des Buchs | Sleeve wieder an | Lag | QQQ dazwischen | Buch dazwischen |
|---|---|---|---|---|---|
| GFC | 2009-05-22 (-19.8%) | 2009-05-22 | 0 d | +0.0% | +0.0% |
| 2011 | 2011-12-28 (-17.9%) | 2011-12-29 | 1 d | +0.7% | +0.1% |
| 2018 Q4 | 2019-06-03 (-21.6%) | 2019-06-04 | 1 d | +2.8% | +1.1% |
| **2020** | 2020-03-19 (-24.5%) | 2020-04-08 | **14 d** | **+13.1%** | +0.7% |
| 2022 | 2023-03-13 (-21.3%) | 2023-03-13 | 0 d | +0.0% | +0.0% |

Das im Auftrag beschriebene Problem („Sleeve reagiert zu langsam") ist in vier von fünf
Episoden **nicht vorhanden** — der Sleeve ist am Tiefpunkt des Buchs bereits wieder an
oder am Folgetag. Es ist ein 2020-Phänomen: eine V-Erholung, in der QQQ 13.1% lief,
bevor der SMA-200 wieder überschritten war.

Eine Heuristik gegen ein einzelnes Ereignis zu bauen, ist definitionsgemäß Curve-Fitting.
Genau daran ist das Vol-Gate am 2026-08-13 gescheitert (an der GFC, die in der
ursprünglichen informellen Betrachtung nicht vorkam).

### Die Arithmetik, die jedes Overlay erledigt

Aus der Fat-Tail-Messung oben: 50 Tage tragen 89% der Rendite. Ein Trigger, der den
Hebel „früher hochfährt", muss zwangsläufig auch die Möglichkeit haben, ihn früher
herunterzufahren — und jede Regel mit dieser Eigenschaft hat eine Trefferwahrscheinlichkeit
gegen sich, die vom Verhältnis 50/4731 bestimmt wird. **Ein Overlay auf diesem
Renditeprofil hat negativen Erwartungswert, es sei denn, es weiß, welche konkreten Tage
es auslässt.** Das ist die quantitative Verallgemeinerung von drei bereits gescheiterten
Versuchen (Tages-Breaker, Stops/TP, Vol-Gate).

### Der eigentliche Compounding-Killer: der HALT ist eine manuelle Einbahntür

Gemessen am 2026-08-12: ein feuernder HALT bei -20% drückt die CAGR von 13.66% auf
**1.00%**. Bei 0.35 feuert er im Sample nie — aber wenn er live feuert, ist die Erholung
**an eine Operator-Handlung gebunden**: `logs/RISK_HALT.lock` *und*
`logs/RISK_HALT_state.json` müssen beide gelöscht werden, sonst feuert der Breaker sofort
wieder (dokumentiert in `deploy/ANLEITUNG.md`).

Das Buch hat historisch -24.8% Drawdown, der Schalter steht bei -35%. Zwischen diesen
beiden Zahlen liegt der einzige Zustand, in dem das System dauerhaft aufhört zu
compounden — und er wird nicht durch Marktmechanik verlassen, sondern dadurch, dass
jemand auf einen Server SSH-t.

**Das ist der größte Renditehebel in Bereich 3, und er ist keine Schwellenwert-Frage.**
Die Schwelle 0.35 bleibt unangetastet; was fehlt, ist eine Re-Arm-*Mechanik*
(z.B. automatische Freigabe, wenn das Equity vom Halt-Tief um einen definierten Betrag
erholt ist, mit Alert statt Blockade). Das verletzt keine der Restriktionen — die
Do-not-tune-Liste schützt Schwellenwerte, nicht das Fehlen eines Reset-Pfads.

---

## 5. Bereich 4 — Capital Inefficiency: die Zerlegung

Mittlere Brutto-Exposure des Buchs: **0.7526**, mittleres Cash **24.74%**.
Verteilung: p5 0.15, p25 0.70, Median 0.85, p75 0.97, p95 0.97, max 1.00.
Cash > 20% an 32.7% der Tage, > 40% an 19.6% der Tage. Am Brutto-Cap (≥0.99): 0.9%.

Zerlegung des mittleren Cash-Anteils (Bucheinheiten; die Posten überlappen, weil sie
gleichzeitig auftreten können):

| Ursache | Ø Gewicht | Bewertung |
|---|---|---|
| Klassenbudget ohne Trend-Mitglied | 0.1635 | **beabsichtigt** — das ist die Trendregel |
| Sleeve aus (QQQ nicht im Trend) | 0.0784 | **beabsichtigt** |
| `per_name_cap`-Kappung (Befund R2) | **0.0582** | **Architekturartefakt**, nicht aktivierbar (Abschn. 1/2) |
| Kern-Caps summieren auf 1.15 > 1.00 | 0.0900 | nur wirksam bei Sättigung |

Rund zwei Drittel des Cash ist die Strategie selbst. Der aktivierbare Rest kostet mehr
Drawdown, als er Rendite bringt (Tabelle in Abschnitt 2).

### Der Posten, der wirklich Geld kostet: die Cash-Verzinsung ist möglicherweise ein Phantom

Der Backtest schreibt dem Leerlauf-Cash ^IRX gut. Gemessener Beitrag:

```
mit ^IRX-Gutschrift : CAGR 12.95%   Sharpe 0.82
mit 0% auf Cash     : CAGR 12.58%   Sharpe 0.80
                      ---------------------------
Differenz             37 bp p.a.   (Ø Cash 24.74% × Ø ^IRX 1.52%)
```

Über den Messzeitraum lag ^IRX im Mittel bei 1.52%. **Heute liegen die kurzen Zinsen bei
~4.3%.** Bei unverändert 24.74% mittlerem Cash entspricht das vorwärts gerichtet
**~106 bp p.a.** — mehr als jede Allokationsvariante in diesem Report erbracht hat.

Die offene Frage ist nicht mathematisch, sondern operativ: **verzinst das reale
Alpaca-Konto den Leerlauf-Bestand?** Wenn nicht, ist dieser Betrag in jeder publizierten
Zahl des Projekts enthalten, ohne live zu existieren. Das ist dieselbe Fehlerklasse wie
die 2 bps Slippage: eine unverifizierte Annahme im Kostenmodell.

Unter der Restriktion „keine neuen Ticker" ist der naheliegende Ausweg (SGOV/BIL als
Cash-Parkplatz) versperrt. Es bleiben drei Optionen, alle Owner-Entscheidungen:
Kontoverzinsung verifizieren und ggf. aktivieren; die Annahme im Backtest auf das
absenken, was das Konto real zahlt; oder die Ticker-Restriktion gezielt für ein
Geldmarktinstrument öffnen.

---

## 6. Rangfolge

| # | Maßnahme | Erwarteter Effekt | Restriktionskonflikt | Evidenzlage |
|---|---|---|---|---|
| 1 | **Befund R1 fixen** (Rundungs-Reject) | stellt Handel an 0.27% der Tage her, systematisch die höchstallokierten | keiner | gemessen, reproduziert |
| 2 | **Cash-Verzinsung des Live-Kontos verifizieren** | bis ~106 bp p.a. Differenz Backtest↔Live | keiner (Verifikation) | gemessen |
| 3 | **Slippage messen** (Phase 3, ≥30 Fills) | validiert/entwertet 2 bps; bei 10 bps Sharpe 1.06→0.93 | keiner | Werkzeug existiert, Daten fehlen |
| 4 | **HALT-Re-Arm-Mechanik** | verhindert den 13.66%→1.00%-Zustand | keiner (Mechanik ≠ Schwelle) | gemessen (2026-08-12) |
| 5 | Dokumentieren, dass P1/P2 inaktiv sind | verhindert Entscheidungen auf falscher Grundlage | keiner | verifiziert |
| — | Allokationsmathematik (Kelly, Momentum-Velocity) | **0**, auf 96.3% der Tage gegenstandslos | — | gemessen |
| — | Caps anheben / Selektor aus | +43…66 bp CAGR, -2…3 pp DD, Calmar ↓ | Cap-Änderung | gemessen |
| — | confirm3 am Sleeve | +30 bp CAGR, **-3.2 pp maxDD** | fällt durch die Pre-Registration-Latte | gemessen |
| — | Same-Close-Ausführung | ~10 bp mechanisch; 39 bp im Backtest nicht robust | keiner | gemessen, instabil |
| — | Limit-Order-Fading | negativer Erwartungswert (Fat Tails) | — | argumentiert aus Messung |

**Zusammenfassung für den Auftrag:** Die vier abgefragten Bereiche enthalten drei
Sackgassen und einen echten Fund. Die Allokationsmathematik hat keine Freiheitsgrade,
weil jede Assetklasse fast immer genau einen Kandidaten hat. Execution-Alpha existiert,
ist aber ~10 bp groß und wird von der Fat-Tail-Struktur der Renditen dominiert, die
passive Ausführung bestraft. Recovery-Beschleunigung scheitert an derselben Arithmetik,
die schon Vol-Gate, Stops und Tages-Breaker erledigt hat. Die tatsächlichen Renditelecks
sind ein Rundungs-Bug, eine womöglich nicht existierende Cash-Verzinsung und eine
unverifizierte Slippage-Annahme — zusammen deutlich mehr wert als jede
Allokationsumstellung, und keines davon berührt eine der harten Restriktionen.

---

## Anhang — Messskripte

Alle im Session-Scratchpad, gegen Produktionscode:

| Skript | Inhalt |
|---|---|
| `diag_capital.py` | fährt `compute_daily_targets` über 4731 Bars, zerlegt Brutto/Cash/ökonomische Exposure, zählt Kappungen, Selektor-Verwürfe, Sleeve-Flips, `validate_book`-Ablehnungen |
| `variants.py` | Varianten A/B/C/D/E + Cash-Yield-Sensitivität |
| `variants2.py` | Varianten F/G/H, Klassen-Slack, Overnight/Intraday-Zerlegung, Same-Close vs. Next-Open |
| `verify.py` | Minimalreproduktion Befund R1, SMA-Nähe-Verteilung, Sleeve-Turnover-Arithmetik |
| `recovery.py` | Sub-Perioden-Robustheit der Ausführung, Wiedereinstiegs-Lags, Fat-Tail-Konzentration |

Einschränkung, die für alles oben gilt: sämtliche Vergleiche sind **in-sample auf
denselben 19.6 Jahren**. Die Block-Bootstrap-Konfidenzintervalle des Projekts über
Trend-Varianten überlappen bekanntermaßen stark. Keine der gemessenen Varianten sollte
ohne neuen, vorab registrierten Test auf ungesehenen Daten übernommen werden — die
Punkte 1–5 der Rangfolge sind bewusst so gewählt, dass sie **Defekte und unverifizierte
Annahmen** betreffen und keine Strategieparameter.

---

# NACHTRAG 2026-08-13 — Punkte 1, 2 und 4 der Rangfolge umgesetzt; Punkt 3 bleibt offen

Owner-Auftrag: die Rangfolge oben umsetzen. Ergebnis:

### Punkt 1 — Befund R1 gefixt

`core/risk_manager.py`: die Cap-Toleranzen in `validate_book()` (Brutto, Per-Name,
ökonomisch, Klasse) waren `1e-9`, obwohl `compose_book()` jedes Gewicht auf 6 Dezimalen
rundet — eine Diskrepanz von drei Größenordnungen. Neue gemeinsame Toleranz
`_WEIGHT_CAP_EPS = 1e-5`, mit Kommentar, der die Herkunft (Rundungsgranularität, nicht
Business-Parameter) dokumentiert. Die Order-/Buying-Power-Prüfungen (Dollar-Ebene) und
die Leverage-Prüfung sind unverändert, da sie nicht auf demselben gerundeten Gewichts-Dict
operieren.

Regressionstest `tests/test_sleeves.py::TestDeployedConfigIsCoherent::
test_composed_book_survives_its_own_rounding` reproduziert den Fund per Brute-Force-Suche
über echte Produktions-Sleeve-Konfiguration (core_scale 0.60, QLD 0.40) — findet ein
Kern-Gewichtstriple, das durch `compose_book()` denselben Wert erzeugt wie im Report
zitiert (`gross = 1.0000010000000001`), und prüft, dass `validate_book()` es jetzt
annimmt. Gegen den alten Code bestätigt fehlgeschlagen (`gross 1.00 exceeds cap 1.00`),
gegen den neuen Code grün — der Test ist also scharf, nicht zufällig grün.

Nebenbefund beim Testen dieses Fixes: `RiskManager._drawdown()` stürzte ab, wenn `.state()`
aufgerufen wurde, bevor jemals `update_equity()` lief (Peak wird beim Konstruieren aus der
Statusdatei geladen, `_current_equity` bleibt aber `None` bis zum ersten `update_equity()`
— ein Pfad, den es vor dem HALT-Re-Arm-Tool unten im Code nirgends gab). Einzeiliger
Guard, Regressionstest `test_state_readable_before_any_equity_update` in
`tests/test_risk.py`.

### Punkt 2 — Cash-Verzinsung verifiziert (nicht nur das Werkzeug gebaut)

`scripts/check_cash_interest.py` neu: fragt `GET /account/activities/INT` über
`AlpacaClient.get_account_activities()` ab (alpaca-py hat dafür keinen typisierten
Wrapper, daher über dessen generisches `.get()`). Lief in dieser Session gegen das in
`.env` dieses Codespace konfigurierte Konto durch — Ergebnis:

```
Account: mode=PAPER  status=ACTIVE  erstellt 2026-06-12  cash=$29,798.76  equity=$99,486.16
17 FILL-Aktivitäten, 0 INT, 0 DIV, 0 CSD/CSW
```

Zwei Monate Kontohistorie, echte Fills, **null** Zinsaktivität. Konsistent mit der im
Skript dokumentierten Erwartung (Alpacas Zins-/Sweep-Programme sind für Live-, nicht für
Paper-Konten dokumentiert). **Vorbehalt:** ungeklärt, ob dies dasselbe Konto ist, das der
`tradingbot`-Server tatsächlich handelt (dieser Codespace sieht dessen `.env`/Zustand
nicht direkt, siehe [[workflow-regime-trader]]) — Kontonummer `PA3IS00108E5`, `id`
`2da87742-a490-42bd-9c3c-2b6dee9deef1`. Owner sollte `scripts/check_cash_interest.py`
einmal direkt auf dem Server laufen lassen, um das zu bestätigen; falls es dieselbe
Kontonummer zeigt, ist die Frage aus Abschnitt 5 beantwortet: die ~106 bp p.a.
^IRX-Gutschrift sind live **Phantom-Rendite**.

### Punkt 3 — Slippage: weiterhin blockiert, keine Kodeänderung

`scripts/slippage_check.py` lief lokal gegen `logs/trades.csv` — 0 verwertbare FILL-Zeilen
(Datei stammt vom 8.7., vor der `expected_price`-Verdrahtung vom 1.8.). Dieser Codespace
hat keinen Zugriff auf die `logs/` des `tradingbot`-Servers. Bleibt offen, bis echte Fills
nach dem 12.8.-Deploy vorliegen (siehe Memory: erster Lauf auf dem neuen Code stand zum
Zeitpunkt dieses Reports noch aus).

### Punkt 4 — HALT-Re-Arm-Mechanik: sicheres manuelles Werkzeug (Owner-Entscheidung)

Vor der Umsetzung nachgefragt, weil eine vollautomatische Wiederaufnahme des Handels nach
einem Max-Drawdown-Halt eine Risikoentscheidung mit echtem Kapital ist, keine, die sich
aus dem Report allein ableiten lässt. Owner-Entscheidung: **sicheres manuelles Werkzeug**,
keine Zeit- oder Recovery-Trigger-Automatik — ein Mensch bestätigt weiterhin jede
Wiederaufnahme.

`scripts/clear_halt.py` neu: zeigt den vollständigen Incident-Report aus
`RISK_HALT.lock`, holt den echten aktuellen Kontostand von Alpaca (oder
`--current-equity` zum Überspringen), zeigt exakt, worauf der Peak re-anchored wird, und
fasst erst nach expliziter Bestätigung (`yes` oder `--yes`) etwas an. Ersetzt die zwei
blinden `sudo rm`-Befehle durch einen Aufruf von `RiskManager.clear_lock()` — der Peak
wird sofort neu verankert statt erst beim nächsten planmäßigen Lauf. `deploy/README.md`
und `deploy/ANLEITUNG.md` aktualisiert (Tool als primärer Weg, manuelles `rm` als
Fallback dokumentiert). End-to-End gegen eine simulierte -36%-Drawdown-Fixture getestet
(Ablehnung bei "no", korrekte Peak-Neuverankerung bei Bestätigung, "nichts zu tun" beim
zweiten Aufruf).

Alle 434 Tests grün, 1 vorbestehend übersprungen (`pytest -q`). Nichts von alledem berührt SMA-200, Vol-Target,
Breaker-Schwellen, 60/40 oder das Universum.

---

## Nachtrag 2026-08-24 — 12,95% ist nicht die kanonische CAGR-Zahl (Befund H3)

Der Vollaudit vom 2026-08-24 listet die 12,95% CAGR oben neben den 13,66%
aus `analysis_report_2026-08-01_deep_review.md` als unreproduzierbaren
Widerspruch und vermutet unterschiedliche Kostenannahmen (10 bp statt 2 bp)
als Ursache. Beide Berichte rechneten tatsächlich mit denselben 2 bp — die
Vermutung ist falsch. Die Kopfzeile dieses Reports (Zeile 9-11) hatte den
echten Grund schon benannt: dieser Report spannte absichtlich ab
**2007-01-03** statt dem natürlichen Indexbeginn 2007-04-10, um einen
Kalenderdatum-Anker für den Varianten-Vergleich in diesem Dokument zu haben.
Nachgerechnet mit `core/portfolio_backtester.py`:

| Span-Wahl | Bars | CAGR |
|---|---:|---:|
| natürlicher Start (2007-04-10) | 4865 | 13,67% |
| `start_date="2007-01-03"` (effektiv ab 2007-10-18) | 4731 | 12,99% |

4731 Bars trifft die hier dokumentierten "4731 Bars nach SMA-Warmup" exakt;
12,99% liegt 4bp über den hier publizierten 12,95% — innerhalb normaler
Datenrevision, keine zusätzliche, unerklärte Lücke. Die **kanonische** Zahl
für das Buch ist die 13,66-13,67%-Reihe (natürlicher Span, keine
`start_date`-Clip), ab jetzt reproduzierbar über `scripts/reproduce_headline.py`.
Die 12,95% in diesem Report bleiben intern für die Varianten-Tabellen gültig
(alle Vergleiche darin nutzen denselben geclippten Span, also denselben
Maßstab), sollten aber nicht als das Buch-Headline-CAGR zitiert werden.
