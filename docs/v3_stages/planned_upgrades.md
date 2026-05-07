# Planned Upgrades for the Trading LLM

## 1. Executive Summary

The current stack is already useful. It can describe market structure, retrieve prices and candles, track the account, surface alerts, and support trade review. That is a solid base. It is not enough for live trading decisions where bad context, bad execution, and bad discipline do more damage than missing one more indicator.

A trading-only LLM is weak if it is only trained on trading logic. Static trading knowledge is not enough. Real usefulness comes from:

- state awareness
- live risk context
- execution feedback
- regime detection
- behavioral enforcement
- post-trade learning loops

That is the gap now. The next upgrades should not chase more indicator wrappers. They should improve decision quality, risk visibility, execution auditing, and trader discipline. If the model can describe a setup but cannot tell whether the book is overexposed, whether the fill quality is trash, whether the regime kills the edge, or whether the trader is repeating stupid behavior, it is still a partial system.

These upgrades are designed for MCP-server exposure and LLM consumption. That means each tool should return explicit, typed, timestamped outputs with traceable inputs, clear freshness, and minimal ambiguity. Vague prose is not enough. The model needs structured state it can reason over without hallucinating missing context.

## 2. Design Principle

The system should evolve from a signal/commentary assistant into a risk-aware decision system.

The system should know:

- what the market is doing
- what regime it is in
- what is already on the book
- what event risk is ahead
- whether a trade fits the playbook
- whether execution quality is acceptable
- whether historical trader behavior supports trusting the setup

The point is not to make the model sound smarter. The point is to make it less blind.

### MCP and LLM Design Constraints

- Every upgrade should expose narrow MCP tools with deterministic schemas.
- Tool outputs should include freshness timestamps, source provenance, and confidence limits where relevant.
- The LLM should not be forced to infer hidden account state, missing event context, or unstated rule logic.
- Guardrail tools should return explicit `pass`, `fail`, and `reason` fields, not soft narrative summaries.
- Review tools should separate facts from interpretation so bad model judgment can be audited.

## 3. Planned Upgrade List

### 3.1 Position and Risk-State Engine

This is non-negotiable. Without it, the model can comment on trades while being blind to portfolio-level stupidity.

#### Problem it solves

- Current trade analysis can look reasonable in isolation while the overall book is badly positioned.
- Traders routinely underestimate cross-pair concentration, correlated stacking, and hidden currency exposure.
- Raw account summary data is not enough for live decision control.

#### Why it matters

- A decent setup can still be the wrong trade if it adds exposure in the wrong place.
- Risk control fails when the system sees single positions instead of the whole book.
- This is the minimum layer required for the model to answer "should I add risk here?" with a serious answer.

#### Core capabilities

- Net exposure by base currency and quote currency
- Correlated exposure across highly related instruments and themes
- Portfolio concentration by pair, sector, currency block, and strategy type
- Open risk grouped by setup, session, and macro theme
- Invalidation distance normalization in price, ATR, and account-risk terms
- Drawdown-state classification for day, week, and rolling period
- Scenario shock view for major USD, rates, commodity, and volatility moves

#### Inputs required

- Open positions
- Open orders
- Account equity, balance, margin, and realized and unrealized PnL
- Instrument metadata, pip value, volatility metrics, and correlation map
- Strategy and setup tags from trade plans or journal records
- Session classification and macro theme mapping

#### Outputs required

- Structured risk snapshot with exposure totals and concentration flags
- Correlation and stacking warnings with severity levels
- Remaining risk budget by day and by strategy bucket
- Scenario stress summary showing likely damage under defined shocks
- MCP-friendly `pass` or `fail` result for proposed new trade risk

#### Key user questions it should answer

- How exposed am I to USD, JPY, metals, or one macro theme right now?
- If I add this trade, what gets worse?
- Which open trades are consuming most of the risk budget?
- Am I in a drawdown state where risk should be cut automatically?

#### Dependencies

- Reliable position and order state
- Instrument registry and pip-value logic
- Correlation model or ruleset
- Trade tagging discipline
- Account-history persistence for drawdown state

#### Implementation notes

- Expose both portfolio snapshot and proposed-trade evaluation tools over MCP.
- Keep the correlation model simple at first. A crude but explicit ruleset is better than fake precision.
- Separate raw exposure math from LLM commentary so numbers remain auditable.
- Include freshness and as-of timestamps on every risk snapshot.

#### Failure modes / risks

- Bad or stale position data makes all downstream conclusions wrong.
- Correlation assumptions can drift across regimes.
- Weak trade tagging will reduce grouping usefulness.
- Scenario shocks can become theater if the scenarios are too generic.

#### Expected impact on PnL

- High. It should improve trade selection and prevent adding bad risk even when the setup itself looks fine.

#### Expected impact on drawdown

- Very high. This is one of the clearest ways to avoid unnecessary drawdown expansion.

#### Estimated implementation difficulty

- High

#### Priority tier

- Tier 1 - non-negotiable

### 3.2 Execution Quality and Slippage Audit

#### Problem it solves

- The system can currently describe entries and outcomes without measuring execution quality.
- A strategy can look profitable on paper while real fills leak the edge away.
- Traders blame strategy logic for losses that actually come from spread, slippage, and bad order handling.

#### Why it matters

- Intended entry is not the same as actual fill.
- Stop execution quality matters as much as entry quality in volatile conditions.
- If execution is leaking edge, more setup analysis is a waste of time.

#### Core capabilities

- Intended entry versus actual fill comparison
- Spread at decision time and at fill time
- Slippage analysis by pair, session, volatility regime, and order type
- Stop-loss execution quality and gap-through tracking
- Immediate adverse excursion after entry
- Broker and venue quality trends over time

#### Inputs required

- Trade plan or intended-entry record
- Broker fill and transaction data
- Tick or sub-bar spread data around decision and fill time
- Regime labels and session labels
- Stop, target, and exit records

#### Outputs required

- Execution audit per trade with fill-quality grading logic kept explicit
- Slippage distribution reports by instrument and context
- Stop-loss execution summary
- Immediate adverse excursion metrics
- Flags showing whether execution is materially degrading expectancy

#### Key user questions it should answer

- Did this trade fail because the idea was wrong or because the fill was bad?
- Which pairs and sessions are producing the worst slippage?
- Are market orders hurting me more than limit orders help me?
- Are stop exits consistently worse than modeled assumptions?

#### Dependencies

- Accurate fill and transaction logs
- Time-synced market data around fill timestamps
- Consistent trade-plan capture before execution
- Regime and session tagging

#### Implementation notes

- Persist intended decision state before order submission. If not, later execution review becomes guesswork.
- Expose one MCP tool for single-trade audit and one for aggregate slippage analytics.
- Use raw price deltas plus normalized measures in pips, basis points, and R multiples.
- Do not hide missing data. Return incomplete status when tick coverage is not good enough.

#### Failure modes / risks

- Missing intended-entry records make attribution weak.
- Bad clock sync will corrupt decision-time versus fill-time comparison.
- Sparse tick data can make spread estimates dishonest.
- Small sample sizes can produce fake conclusions by pair or order type.

#### Expected impact on PnL

- High. It identifies direct edge leakage that standard journaling misses.

#### Expected impact on drawdown

- Medium to high. Better execution and order selection reduce damage during poor conditions.

#### Estimated implementation difficulty

- High

#### Priority tier

- Tier 1 - non-negotiable

### 3.3 Regime Classification Engine

Setup edge is regime-conditional. A setup that works in expansion can fail repeatedly in compression. Pretending otherwise is lazy.

#### Problem it solves

- Current analysis can identify patterns without formally classifying the market regime around them.
- Traders often apply the same playbook across trend, chop, panic, and event conditions as if they are interchangeable.
- Static setup logic overfits to ideal conditions and degrades fast in live trading.

#### Why it matters

- Regime determines whether a pattern has room to work.
- Volatility and liquidity conditions change stop behavior, target behavior, and fill quality.
- Without regime awareness, the LLM is just narrating candles.

#### Core capabilities

- Trend, mean-reversion, compression, and expansion classification
- High-volatility versus low-volatility classification
- Risk-on versus risk-off backdrop labeling
- Event-driven versus technically-driven condition labeling
- Liquidity condition detection across sessions and rollover windows
- Regime transition detection and instability warnings

#### Inputs required

- Multi-timeframe price structure
- Volatility measures
- Session and liquidity data
- Macro-event schedule
- Cross-market reference data
- Spread and execution condition data

#### Outputs required

- Current regime label with supporting factors
- Regime transition risk score or state
- Per-setup compatibility map
- Warnings when a proposed trade conflicts with known regime constraints
- Historical regime tag attached to trades and reviews

#### Key user questions it should answer

- What regime am I trading right now?
- Does this setup have a real edge in this regime?
- Is this market technically driven or waiting for event repricing?
- Is current liquidity good enough for this playbook?

#### Dependencies

- Stable volatility and structure calculations
- Macro-event context
- Cross-market context inputs
- Historical trade outcomes tagged by regime

#### Implementation notes

- Start with explicit rule-based classification before trying learned models.
- Return both regime label and the reasons behind it so failures can be audited.
- Avoid overfitting. A smaller set of durable regime buckets is better than a taxonomy nobody can trust.
- Expose one current-state tool and one historical-regime-lookup tool over MCP.

#### Failure modes / risks

- Too many regime categories will reduce reliability.
- Poor feature selection will create unstable labels.
- Regime classifications can lag actual market transitions.
- Users may over-trust neat labels that hide uncertainty.

#### Expected impact on PnL

- High. It should improve setup selection and reduce mismatch between playbook and market condition.

#### Expected impact on drawdown

- High. It cuts down repeated losses from trading the wrong idea in the wrong environment.

#### Estimated implementation difficulty

- Medium to high

#### Priority tier

- Tier 1 - non-negotiable

### 3.4 Trade Journal Intelligence / Behavioral Diagnostics

This is about separating real edge from fake edge and business losses from stupidity.

#### Problem it solves

- Basic journaling stores history but does not diagnose process quality.
- Traders can repeat the same mistakes while convincing themselves they are just unlucky.
- PnL alone does not show whether the underlying process is improving or decaying.

#### Why it matters

- Good trading requires behavior review, not just market review.
- A bad process can produce short-term wins and a good process can still take losses.
- The model should detect when behavior is breaking the edge before the account proves it expensively.

#### Core capabilities

- Setup tagging and tag hygiene checks
- Thesis-quality review
- Invalidation-quality review
- Hold-time behavior analysis
- Exit-quality assessment versus plan and context
- Rule-violation detection
- Emotional or process-breakdown tagging from notes and behavior patterns

#### Inputs required

- Trade journal records
- Trade plans and setup tags
- Entry, stop, exit, and time-in-trade data
- Notes and post-trade commentary
- Account and drawdown state at trade time
- Rule-engine violations and override events

#### Outputs required

- Behavioral diagnostics summary by period and setup type
- Rule-violation log with severity
- Process-quality scores broken into explicit components
- Repeat-error pattern detection
- Journal prompts for missing or weak trade records

#### Key user questions it should answer

- Am I actually following the playbook?
- Which losses are normal business losses and which are avoidable stupidity?
- Does this setup still have edge when I remove rule-breaking trades?
- Are my exits consistently worse than my plan?

#### Dependencies

- Good journal coverage
- Reliable trade-plan and execution records
- Rule-engine outputs
- Storage for structured notes and tags

#### Implementation notes

- Keep scoring decomposed. One opaque score is useless.
- Use note parsing carefully. Text notes are valuable, but behavior events should carry more weight than self-description.
- Expose trade-level diagnostics and periodic review summaries as separate MCP tools.
- Build for missing data explicitly. Most journals are incomplete.

#### Failure modes / risks

- Weak or inconsistent note-taking reduces diagnostic depth.
- Users may game the tagging system if it becomes punitive without being useful.
- Over-scoring soft signals can turn review into fake science.
- If rule definitions are vague, diagnostics will be vague too.

#### Expected impact on PnL

- High over time. It helps preserve real edge and strip out self-inflicted losses.

#### Expected impact on drawdown

- High. Behavioral mistakes tend to cluster during bad periods, which is exactly when this tool matters most.

#### Estimated implementation difficulty

- Medium

#### Priority tier

- Tier 1 - non-negotiable

### 3.5 Rule Engine / Trade Guardrails

The model must sometimes tell the trader "no". If it cannot block obvious bad behavior, it is not protecting anything.

#### Problem it solves

- Informational tools can warn about problems without enforcing anything.
- Traders break plans most aggressively when stressed, overconfident, or trying to get losses back fast.
- A passive assistant is too easy to ignore.

#### Why it matters

- Real risk control needs hard stops, not polite suggestions.
- The system should prevent predictable stupidity before it hits the book.
- Guardrails create consistency when judgment is degraded.

#### Core capabilities

- Blocking trades outside the stated plan
- Blocking correlated stacking
- Blocking pre-event entries inside defined event-risk windows
- Blocking revenge trading after drawdown or loss streak conditions
- Blocking low-liquidity participation
- Requiring explicit override logging for exceptional cases

#### Inputs required

- Approved playbook definitions
- Risk-state engine outputs
- Event calendar and event exposure map
- Session and liquidity state
- Trade journal and recent behavior state
- Proposed trade payload with setup, size, timing, and rationale

#### Outputs required

- Allow, block, or allow-with-warning decision
- Rule-trigger list with explicit reasons
- Required override record when a block is bypassed
- Audit trail linking decisions to rule versions
- Daily and weekly violation summaries

#### Key user questions it should answer

- Can I take this trade right now?
- Which rule is blocking me?
- Is this a legitimate exception or a dumb impulse?
- Am I tightening or loosening discipline compared with the last month?

#### Dependencies

- Position and risk-state engine
- Event and liquidity context
- Clear playbook definitions
- Journal and drawdown-state tracking

#### Implementation notes

- Keep the first version deterministic and rule-based.
- Rules should be versioned and traceable. Hidden guardrail logic is not acceptable.
- Expose pre-trade validation as a fast MCP call suitable for routine use.
- Distinguish advisory warnings from true blocks. Mixing them weakens both.

#### Failure modes / risks

- Bad rule design can create useless friction.
- Too many overrides will expose that the system has no real authority.
- Vague playbooks will cause noisy false blocks.
- If the risk-state engine is wrong, guardrails will be wrong too.

#### Expected impact on PnL

- High. It mainly improves avoided losses rather than finding more trades.

#### Expected impact on drawdown

- Very high. This is one of the strongest drawdown-control tools in the stack.

#### Estimated implementation difficulty

- Medium

#### Priority tier

- Tier 1 - non-negotiable

### 3.6 Replay and Counterfactual Simulator

This is how theory gets tested against market reality instead of being defended with excuses.

#### Problem it solves

- Post-trade review often stops at "won" or "lost" without testing better or worse execution paths.
- Traders keep debating alternate entries, stops, and exits without structured evidence.
- Strategy refinement stays theoretical when the system cannot replay decisions against actual candles.

#### Why it matters

- Many process improvements are obvious only in replay.
- Expectancy changes often come from execution rules, not setup discovery.
- Counterfactual testing helps separate robust ideas from hindsight storytelling.

#### Core capabilities

- Candle-by-candle replay
- Alternate entry-logic testing
- Alternate stop-logic testing
- Alternate exit-logic testing
- Scale-out and scale-in testing
- Expectancy comparison across rule variants

#### Inputs required

- Historical candles with integrity checks
- Original trade records
- Entry, stop, and exit rule definitions
- Regime labels
- Execution cost assumptions or actual execution stats

#### Outputs required

- Replay timeline
- Counterfactual trade variants
- Expectancy comparison reports
- Sensitivity analysis on stop and exit policies
- Notes on whether the original trade was process-correct even if suboptimal

#### Key user questions it should answer

- Would this trade have improved with a different entry or stop?
- Is my scale-out logic helping or just making me feel safer?
- Which exit rule actually improves expectancy across enough samples?
- Did I execute the playbook well even if the result was poor?

#### Dependencies

- Clean historical candle store
- Journal and execution records
- Regime tags
- Data-quality monitoring

#### Implementation notes

- Keep counterfactual assumptions explicit. Hidden assumptions turn replay into fiction.
- Support both single-trade replay and batch policy comparison.
- Use actual execution-cost distributions where possible instead of idealized fills.
- This tool is strongest after execution audit and regime classification exist.

#### Failure modes / risks

- Hindsight bias can contaminate replay interpretation.
- Replay quality collapses if candle integrity is weak.
- Too many simulation knobs will invite overfitting.
- If cost assumptions are fake, conclusions will be fake.

#### Expected impact on PnL

- Medium to high. Useful for process refinement and policy improvement rather than immediate risk control.

#### Expected impact on drawdown

- Medium. Better exits and stops help, but the effect depends on disciplined adoption.

#### Estimated implementation difficulty

- High

#### Priority tier

- Tier 2 - strong edge

### 3.7 Macro-Event Exposure Mapper

A raw event list is not enough. Relevance matters.

#### Problem it solves

- Economic calendar access tells the system what events exist, not which ones matter for the book.
- Traders often underestimate second-order exposure through USD, rates, and risk sentiment channels.
- Event risk is routinely treated as binary when it is instrument-specific and context-dependent.

#### Why it matters

- The market does not care about all events equally.
- Event exposure should be mapped to open trades and proposed trades, not dumped as a list.
- A position can be indirectly exposed even when the headline does not name the instrument.

#### Core capabilities

- Mapping open trades to upcoming events
- First-order and second-order exposure analysis
- Event relevance scoring by instrument and theme
- Assessment of whether the market is already positioned for the event
- Event-window risk flags for new entries and existing positions

#### Inputs required

- Economic calendar
- Open positions and pending orders
- Instrument-to-macro sensitivity map
- Cross-market positioning signals
- Recent price-action and volatility context

#### Outputs required

- Event exposure map for current book
- Relevance-ranked event list
- Per-trade event-risk summary
- Block or warning flags for event-window participation
- Notes on consensus, crowding, and likely market sensitivity

#### Key user questions it should answer

- Which upcoming events actually matter for my open trades?
- Is this pair directly exposed, indirectly exposed, or mostly unaffected?
- Is the market already positioned for this event?
- Should I reduce risk or avoid opening a trade before this release?

#### Dependencies

- Calendar refresh and normalization
- Position and risk-state engine
- Cross-market relationship engine
- Macro theme mapping

#### Implementation notes

- Start with explicit relevance rules by instrument family before trying probabilistic relevance models.
- Separate event importance from event relevance. High-importance events are not equally relevant to every trade.
- Expose book-level event map and trade-level relevance check as separate MCP tools.
- Freshness matters. Event timing errors make the tool dangerous.

#### Failure modes / risks

- Relevance mapping can become too generic and noisy.
- Event schedules can revise or drift across sources.
- "Market already positioned" is easy to oversimplify.
- Second-order exposure can be missed if the cross-market engine is weak.

#### Expected impact on PnL

- Medium to high. It mainly improves trade timing and avoids dumb event exposure.

#### Expected impact on drawdown

- High around event-heavy periods where gaps and repricing matter most.

#### Estimated implementation difficulty

- Medium

#### Priority tier

- Tier 2 - strong edge

### 3.8 Cross-Market Relationship Engine

Single-chart analysis is often incomplete. Many FX and metals trades are downstream expressions of broader moves.

#### Problem it solves

- Pair-level analysis can miss the driver behind the move.
- Without cross-market context, the model can mistake symptom for cause.
- Macro regime interpretation stays shallow if it ignores related assets and rates.

#### Why it matters

- DXY, yields, VIX, equities, crude, and metals relationships often frame whether a trade idea is aligned or fighting the tape.
- Cross-market context helps distinguish local chart structure from broader macro flow.
- It improves both regime detection and event relevance mapping.

#### Core capabilities

- DXY relationship mapping
- Yield and real-yield relationship mapping
- VIX and equity risk-sentiment relationship mapping
- Crude sensitivity mapping where relevant
- Index, metals, and energy context
- Copper and growth-sensitivity context
- Rate-spread context for FX pairs

#### Inputs required

- Price series for reference assets
- Yield and rate-spread data
- Instrument sensitivity map
- Session and macro-event context
- Regime labels and volatility state

#### Outputs required

- Cross-market alignment summary
- Divergence and confirmation flags
- Relationship snapshots by instrument
- Macro-driver notes with explicit evidence
- Inputs usable by regime and event tools

#### Key user questions it should answer

- Is this move aligned with the usual macro drivers?
- Are yields, DXY, and risk sentiment confirming or contradicting the setup?
- Is SPX500_USD moving on USD weakness, real yields, risk aversion, or something else?
- Which cross-market relationships matter most for this instrument right now?

#### Dependencies

- Reliable external market data
- Instrument sensitivity mapping
- Regime engine
- Macro-event mapping

#### Implementation notes

- Do not dump all relationships for every instrument. Relevance ranking is mandatory.
- Return evidence fields, not just narrative interpretation.
- Use rolling relationship diagnostics carefully; correlation alone is not enough.
- Build this as context enrichment, not as a magical predictor.

#### Failure modes / risks

- Relationship drift across regimes can mislead naive models.
- Too much context will produce noise instead of clarity.
- External data freshness problems will poison conclusions.
- Users may overfit to familiar macro stories.

#### Expected impact on PnL

- Medium. It sharpens selection and timing more than it directly creates edge.

#### Expected impact on drawdown

- Medium. It helps avoid fighting broad drivers, especially in macro-heavy periods.

#### Estimated implementation difficulty

- Medium to high

#### Priority tier

- Tier 2 - strong edge

### 3.9 Data Quality and Market Integrity Monitor

Smart models with dirty data become confident idiots.

#### Problem it solves

- All higher-level analysis depends on data that can silently degrade.
- Bad candles, duplicate bars, missing bars, spread spikes, stale ticks, and feed distortion can corrupt every decision layer.
- Data issues often look like strategy failure until someone checks the plumbing.

#### Why it matters

- Model quality does not survive input corruption.
- Risk, execution, and journal analytics become misleading when timestamps or prices are wrong.
- This is defensive infrastructure that prevents fake intelligence.

#### Core capabilities

- Bad-candle detection
- Duplicate-bar detection
- Missing-bar detection
- Spread-anomaly detection
- Stale-tick detection
- Broker-feed distortion detection
- Timezone and clock-skew checks

#### Inputs required

- Candle store
- Tick or quote stream
- Broker metadata
- Source timestamps and local ingest timestamps
- Reference feeds where available

#### Outputs required

- Data-health status by source and instrument
- Integrity flags attached to downstream analytics
- Alerting for data anomalies
- Quarantine recommendations for suspect windows
- Audit logs for corrected or excluded data

#### Key user questions it should answer

- Can I trust this candle set or execution review?
- Is this spread spike real or a feed artifact?
- Did clock drift break the timing on this event or fill analysis?
- Which instruments or providers are currently degraded?

#### Dependencies

- Stable ingest logging
- Reference or secondary validation source where possible
- Storage for anomaly history
- Downstream consumers that honor integrity flags

#### Implementation notes

- This should run continuously, not just during postmortems.
- Downstream tools should be required to surface integrity warnings instead of ignoring them.
- Start with simple rule checks and expand only where false positives are manageable.
- MCP outputs should include a hard trust status that other tools can consume.

#### Failure modes / risks

- Overly sensitive anomaly rules can create alert fatigue.
- No reference feed means some distortions will remain ambiguous.
- If downstream tools ignore integrity flags, this becomes cosmetic.
- Timezone bugs are subtle and easy to reintroduce.

#### Expected impact on PnL

- Indirect but important. It prevents bad decisions based on broken inputs.

#### Expected impact on drawdown

- Medium. Mostly protective by reducing error-driven losses and false conclusions.

#### Estimated implementation difficulty

- Medium

#### Priority tier

- Tier 3 - robustness and context

### 3.10 Structural Market Context Engine

Setup location matters as much as setup pattern.

#### Problem it solves

- A setup can be technically valid but poorly located inside the broader structure.
- Current analysis may identify patterns without enough higher-order context about range position, balance, and value migration.
- Traders often overfocus on trigger patterns and underweight location.

#### Why it matters

- Higher-timeframe location changes trade quality materially.
- Prior session highs and lows, dealing ranges, VWAP relationship, and value migration often determine whether the setup has room or runs into traffic.
- Carry and rate-differential context matter for some instruments even when the chart looks clean.

#### Core capabilities

- Higher-timeframe dealing-range mapping
- Weekly and monthly balance versus imbalance detection
- Prior-session highs and lows
- Liquidity-pool mapping
- VWAP relationship and displacement context
- Value-migration context
- Carry and rate-differential context

#### Inputs required

- Multi-timeframe candles
- Session profile or value references where available
- VWAP inputs
- Prior-session and higher-timeframe reference levels
- Rates and carry data for relevant instruments

#### Outputs required

- Structural-location summary
- Range-position and imbalance flags
- Nearby liquidity and traffic map
- VWAP and value-migration status
- Carry-context notes where relevant

#### Key user questions it should answer

- Is this setup occurring in a good location or in the middle of nowhere?
- Is price trading from imbalance into balance, or vice versa?
- Are nearby prior-session highs or lows likely to cap the move?
- Does carry context support holding this trade longer?

#### Dependencies

- Clean multi-timeframe data
- Session context
- Structural analysis stack
- Rates data for carry-sensitive instruments

#### Implementation notes

- Keep location outputs concrete and level-based.
- Avoid turning this into another indicator pack.
- Expose one snapshot tool for current structural context and one comparator tool for proposed trade location.
- This engine should feed the regime, rule, and trade-review layers.

#### Failure modes / risks

- Overcomplicated structural labels will reduce usability.
- VWAP and value context depend on consistent session handling.
- Carry inputs can be stale or too slow-moving for intraday relevance.
- Users may confuse context with a standalone entry signal.

#### Expected impact on PnL

- Medium. Mostly improves trade filtering and target realism.

#### Expected impact on drawdown

- Medium. Better location reduces low-quality entries and poor holding decisions.

#### Estimated implementation difficulty

- Medium

#### Priority tier

- Tier 3 - robustness and context

## 4. For Each Planned Tool, Include the Same Subsections

Each tool above uses the same operating structure so contributors do not hide weak thinking behind uneven documentation. Every planned tool should define:

- Problem it solves
- Why it matters
- Core capabilities
- Inputs required
- Outputs required
- Key user questions it should answer
- Dependencies
- Implementation notes
- Failure modes / risks
- Expected impact on PnL
- Expected impact on drawdown
- Estimated implementation difficulty
- Priority tier

Across all of them, the same delivery standard should apply:

- MCP-first design with typed request and response schemas
- Explicit freshness timestamps and source provenance
- Separate factual output from model interpretation
- Support both single-trade and portfolio or period-level queries where relevant
- Store enough raw state for later audit instead of recomputing from vague summaries
- Fail loudly on missing or degraded inputs instead of pretending everything is fine

## 5. Priority Roadmap

| Upgrade | Priority Tier | Expected Impact on PnL | Expected Impact on Drawdown | Usage Frequency | Build Difficulty | Why it should be built now |
|---|---|---|---|---|---|---|
| Position and Risk-State Engine | Tier 1 | High | Very high | Every trading session | High | Without portfolio-level risk awareness, the model cannot evaluate new trades seriously. |
| Execution Quality and Slippage Audit | Tier 1 | High | Medium to high | Daily and post-trade | High | It shows whether edge is being lost in execution instead of strategy logic. |
| Regime Classification Engine | Tier 1 | High | High | Every trading session | Medium to high | Setup quality is regime-dependent; this should sit underneath most decision logic. |
| Trade Journal Intelligence / Behavioral Diagnostics | Tier 1 | High over time | High | Daily and weekly review | Medium | It distinguishes real edge from self-inflicted damage and keeps review honest. |
| Rule Engine / Trade Guardrails | Tier 1 | High | Very high | Every proposed trade | Medium | It converts passive advice into actual discipline and should block obvious bad behavior. |
| Replay and Counterfactual Simulator | Tier 2 | Medium to high | Medium | Weekly review and strategy work | High | It turns trade review into evidence instead of hindsight arguments. |
| Macro-Event Exposure Mapper | Tier 2 | Medium to high | High around events | Daily and pre-event | Medium | The system needs relevance-aware event mapping, not a raw calendar dump. |
| Cross-Market Relationship Engine | Tier 2 | Medium | Medium | Daily and pre-trade | Medium to high | Single-chart analysis is incomplete when macro drivers are doing the real work. |
| Data Quality and Market Integrity Monitor | Tier 3 | Indirect but important | Medium | Continuous | Medium | Dirty inputs make every higher-order tool less trustworthy. |
| Structural Market Context Engine | Tier 3 | Medium | Medium | Daily and pre-trade | Medium | Better location context improves filtering, target logic, and holding decisions. |

## 6. Cheapest Workable Stack

Build this stack with a zero-software-budget bias first. Spend money later only if a real bottleneck is proven. Vendor pricing and free-tier limits change, so verify them at implementation time before committing to any dependency.

The main principle is simple:

- use OANDA as the single source of truth for live trading state
- record your own quotes and spreads
- use free macro and proxy data only where it does not control hard execution decisions
- avoid paid feeds until the risk engine and execution audit prove they are the constraint

### Core Python packages

All of the following are free and sufficient for a first serious implementation:

```txt
pandas
numpy
pydantic
pydantic-settings
httpx
websockets
sqlalchemy
psycopg
redis
fastapi
uvicorn
pytest
freezegun
python-dotenv
pyarrow
duckdb
APScheduler
structlog
prometheus-client
```

### Data, API, and subscription choices

| Need | Cheapest workable option | Notes |
|---|---|---|
| Broker account, trades, orders, candles, live prices | OANDA | Good enough for the first serious build. Do not pay for another broker feed unless OANDA is actually blocking you. |
| Fill and spread history for execution audit | Self-captured OANDA bid and ask stream | Best cheap option. If you do not record this yourself, the execution audit will be weak or fake. |
| FX and metals historical candles | OANDA candles | Good enough initially. |
| Yields and macro time series | FRED API | Strong free source for rates and macro context. |
| DXY, VIX, equities, commodity, and futures proxies | `yfinance` | Fine for prototype and context enrichment. Not a production-grade intraday truth source. |
| Backup daily bars | Stooq or Alpha Vantage if a usable free tier exists at implementation time | Useful only as a loose fallback, not as primary truth. |
| Economic calendar | Curated internal calendar sourced from official releases | Cheapest serious option. Better than fragile scraping. |
| News | Skip at first | Correct tradeoff. Generic news is a weak use of time here. |
| Secondary validation feed | None at first, or free daily backup data | Acceptable early. Build integrity checks before paying for redundant feeds. |

### Cheapest build choices by subsystem

#### Broker, execution, and account state

Use OANDA only for:

- account summary
- open positions
- open orders
- transactions
- pricing stream
- candles

Hard truth:

- Do not buy another broker feed early unless OANDA is actually failing the use case.

#### Execution-quality and slippage audit

Cheapest serious option:

- capture your own bid and ask history from the OANDA live stream

Store:

- bid
- ask
- spread
- timestamp
- instrument

At trade time, also persist:

- decision time
- submit time
- fill time

Then compare fills against stored quotes. Without this, execution review is mostly guesswork.

Suggested implementation pieces:

- `websockets` or the OANDA pricing stream client
- PostgreSQL or Parquet storage
- async worker or scheduled maintenance jobs

#### Economic calendar

This is the weakest part of a free stack.

Cheapest serious option:

- build a curated internal calendar from official release sources for only the events that actually matter

Start with:

- FOMC
- CPI
- PPI
- NFP
- Retail Sales
- ISM
- GDP
- central bank decisions
- major speeches
- BoE, ECB, and BoJ events
- high-impact China releases if trading metals, AUD, or NZD

Use official pages from institutions such as:

- Federal Reserve
- BLS
- BEA
- Census
- ECB
- BoE
- BoJ
- ONS
- Eurostat
- Statistics Canada
- RBA
- ABS

Best zero-cost compromise for v1:

- maintain event time, currency, impact, and category first
- add consensus, prior, and actual later if the system truly needs them

Hard truth:

- A fully free, fully structured, high-quality macro calendar is one of the hardest parts to do well cheaply.

#### Cross-market reference data

Use a split stack:

- FRED API for yields, rate spreads, and macro series
- `yfinance` for VIX, DXY proxy, SPY and QQQ proxies, crude proxies, copper proxies, and index or metals futures proxies

Verdict:

- FRED is strong for free
- `yfinance` is acceptable for context and research, not for hard execution logic

#### Secondary validation feed

Cheapest option:

- skip it first
- add a free daily backup source later only if needed

Possible fallback sources:

- `yfinance`
- Stooq
- Alpha Vantage if the free tier is still usable at implementation time

Hard truth:

- Free secondary intraday validation is usually mediocre. A data-integrity monitor is a better first investment.

#### Database and storage

Use PostgreSQL for:

- trades
- orders
- fills
- journal entries
- rule violations
- risk snapshots
- regime labels
- trade intents

Use Redis for:

- cache
- alert queues
- fast state

Use Parquet on disk for:

- candles
- quote history
- replay datasets
- slippage study data

Optional:

- DuckDB for local analytics and research

#### API and service layer

Use:

- FastAPI for internal APIs and MCP-facing wrappers
- APScheduler or plain cron for jobs
- Docker Compose for local deployment
- Uvicorn for serving

#### Monitoring and logging

Use:

- Prometheus for metrics
- Grafana for dashboards
- `structlog` or standard logging
- Uptime Kuma for service monitoring

Optional:

- Sentry free tier if a usable free tier exists at implementation time

#### Secrets and config

Use:

- `.env`
- `pydantic-settings`

Hard truth:

- Do not waste time on Vault early unless the deployment shape actually requires it.

#### Journal and diagnostics

No paid subscription is needed.

Use:

- PostgreSQL tables
- Pydantic schemas
- internal tags, rules, notes, and review records

That is enough to cover:

- setup tags
- rule violations
- exits
- hold time
- emotional notes
- post-trade review

### Cheapest build by planned tool

| Planned tool | Cheapest workable build |
|---|---|
| Position and Risk-State Engine | OANDA + PostgreSQL + internal risk math |
| Execution Quality and Slippage Audit | OANDA stream self-capture + trade-intent logging |
| Regime Classification Engine | OANDA candles + FRED + simple rule-based logic |
| Trade Journal Intelligence / Behavioral Diagnostics | PostgreSQL + internal schemas |
| Rule Engine / Trade Guardrails | YAML or JSON playbook rules + internal service |
| Replay and Counterfactual Simulator | OANDA historical candles + Parquet + pandas |
| Macro-Event Exposure Mapper | Curated official calendar + internal relevance rules |
| Cross-Market Relationship Engine | FRED + `yfinance` |
| Data Quality and Market Integrity Monitor | Internal checks on stored data |
| Structural Market Context Engine | OANDA candles + existing structure and VWAP logic |

### Where free is good enough

Free is good enough for:

- Python libraries
- database
- cache
- API framework
- scheduler
- logging and monitoring
- replay engine
- journaling
- rule engine
- risk engine
- OANDA-based live trading state
- FRED-based macro and yields

### Where free starts to break down

Free gets weak fastest in:

- structured economic calendar quality
- clean cross-market intraday data
- secondary validation feeds
- robust historical bid and ask tick archives

The cheapest serious workaround is to self-capture your own data, especially:

- bid and ask stream history
- spreads
- candles
- event windows
- trade-intent state

### What not to pay for at the start

Do not spend money first on:

- news terminals
- alternative data
- fancy ML platforms
- premium backtesting stacks
- more indicator APIs
- expensive vector database tooling

That is distraction. The first paid spend should come only after the risk engine and execution audit are already running and a real data constraint is obvious.

### Lean stack summary

If the goal is the cheapest real build, use:

- OANDA for broker, account, candles, and pricing
- FRED for rates and macro
- `yfinance` for non-critical cross-market context
- PostgreSQL
- Redis
- Parquet
- FastAPI
- Docker Compose
- Prometheus and Grafana
- APScheduler
- an internal curated macro calendar
- an internal quote-capture service
- an internal trade-intent logger

Software cost should stay near zero, excluding:

- machine or VPS cost
- OANDA account requirements
- any future paid feeds added after the system proves they are needed

## 7. What Not to Prioritize

The following are weaker uses of build time right now:

- more indicator wrappers
- generic news summarization
- vague "AI insights" without traceability

Why these are lower priority:

- More indicator wrappers mostly add surface area, not decision quality. The stack already has enough pattern and indicator coverage to be dangerous without stronger state and risk control.
- Generic news summarization creates noise unless it is tied directly to event relevance, position exposure, and regime impact.
- Vague AI insights are not operational tools. If the system cannot show what facts drove the conclusion, it becomes hard to trust and impossible to debug.

## 8. Suggested Build Sequence

### Phase 1: risk and discipline foundation

- Build the Position and Risk-State Engine.
- Build the Rule Engine / Trade Guardrails on top of that state.
- Build the Trade Journal Intelligence baseline so discipline and violations are captured immediately.

### Phase 2: execution and review intelligence

- Build the Execution Quality and Slippage Audit.
- Build the Regime Classification Engine.
- Connect regime, execution, and journal outputs into recurring review workflows.

### Phase 3: macro/cross-market context enrichment

- Build the Macro-Event Exposure Mapper.
- Build the Cross-Market Relationship Engine.
- Build the Structural Market Context Engine where it materially improves location and hold logic.

### Phase 4: simulation and continuous improvement loop

- Build the Replay and Counterfactual Simulator.
- Build or tighten the Data Quality and Market Integrity Monitor so replay and review are trustworthy.
- Use the full loop to connect idea quality, execution quality, behavioral quality, and adaptation.

## 9. Closing Conclusion

The goal is not to build a smarter chart commentator. The goal is to build a risk-aware trading decision system that improves judgment, limits stupidity, and closes the loop between idea, execution, review, and adaptation.
