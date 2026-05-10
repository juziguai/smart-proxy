# Claude Code Usage Source Notes

This document records the Claude Code token/statistics logic found in the local
Claude Code source package under `D:\Tools\AI\Claude-code\Claude-Code-*`. It is
meant as reusable context for future smart-proxy work. The source package is
bundled/minified, but
`package/cli.js.map` contains original source paths and source contents.

## Scope

The investigation intentionally focused only on usage, token accounting,
statistics cache, and transcript persistence. It did not review the full Claude
Code codebase.

Relevant logical source files from the source map:

- `../src/services/api/claude.ts`
- `../src/services/api/logging.ts`
- `../src/services/api/emptyUsage.ts`
- `../src/cost-tracker.ts`
- `../src/utils/stats.ts`
- `../src/utils/statsCache.ts`
- `../src/components/Stats.tsx`
- `../src/QueryEngine.ts`
- `../src/utils/sessionStoragePortable.ts`
- `../src/utils/envUtils.ts`

## Main Conclusion

Claude Code already receives authoritative token usage from the model API and
persists it into local transcript records. A smart-proxy dashboard does not need
to decrypt HTTPS traffic to obtain token statistics. It can read Claude Code's
local JSONL transcripts and aggregate the `message.usage` fields.

This changes the implementation strategy significantly:

- Request count, success/failure, routing, and latency should come from
  smart-proxy itself.
- Token usage should come from Claude Code transcript files.
- No MITM certificate, TLS interception, or API response parsing inside
  smart-proxy is required.

## Claude Config And Transcript Location

Claude Code computes its config home with `getClaudeConfigHomeDir`:

- Use `CLAUDE_CONFIG_DIR` if present.
- Otherwise use `join(homedir(), '.claude')`.

Logical source:

```ts
export const getClaudeConfigHomeDir = memoize(
  (): string => {
    return (
      process.env.CLAUDE_CONFIG_DIR ?? join(homedir(), '.claude')
    ).normalize('NFC')
  },
  () => process.env.CLAUDE_CONFIG_DIR,
)
```

Transcript projects live under:

```text
<claude-config-home>/projects
```

`getProjectsDir()` returns:

```ts
export function getProjectsDir(): string {
  return join(getClaudeConfigHomeDir(), 'projects')
}
```

Claude Code statistics scans:

- Main session files: `<config>/projects/<project-dir>/*.jsonl`
- Subagent files:
  `<config>/projects/<project-dir>/<session-id>/subagents/agent-*.jsonl`

## Usage Object Shape

Claude Code initializes empty usage as:

```ts
export const EMPTY_USAGE: Readonly<NonNullableUsage> = {
  input_tokens: 0,
  cache_creation_input_tokens: 0,
  cache_read_input_tokens: 0,
  output_tokens: 0,
  server_tool_use: { web_search_requests: 0, web_fetch_requests: 0 },
  service_tier: 'standard',
  cache_creation: {
    ephemeral_1h_input_tokens: 0,
    ephemeral_5m_input_tokens: 0,
  },
  inference_geo: '',
  iterations: [],
  speed: 'standard',
}
```

Fields useful for smart-proxy reporting:

- `input_tokens`
- `output_tokens`
- `cache_creation_input_tokens`
- `cache_read_input_tokens`
- `server_tool_use.web_search_requests`
- `server_tool_use.web_fetch_requests`
- `service_tier`
- `cache_creation.ephemeral_1h_input_tokens`
- `cache_creation.ephemeral_5m_input_tokens`
- `speed`

The API may also expose additional internal fields such as
`cache_deleted_input_tokens` behind feature gates, but smart-proxy should not
depend on them for a first implementation.

## Streaming Usage Update Logic

In `claude.ts`, streaming starts from `EMPTY_USAGE` and updates usage from
`message_start` and `message_delta` events.

Important behavior:

- `input_tokens`, cache creation, and cache read tokens are only overwritten
  when the new value is non-null and greater than zero.
- `output_tokens` is updated from delta usage when present.
- Cache creation ephemeral fields are present in real API responses even if SDK
  types do not expose them cleanly.

Logical update function:

```ts
export function updateUsage(
  usage: Readonly<NonNullableUsage>,
  partUsage: BetaMessageDeltaUsage | undefined,
): NonNullableUsage {
  if (!partUsage) {
    return { ...usage }
  }
  return {
    input_tokens:
      partUsage.input_tokens !== null && partUsage.input_tokens > 0
        ? partUsage.input_tokens
        : usage.input_tokens,
    cache_creation_input_tokens:
      partUsage.cache_creation_input_tokens !== null &&
      partUsage.cache_creation_input_tokens > 0
        ? partUsage.cache_creation_input_tokens
        : usage.cache_creation_input_tokens,
    cache_read_input_tokens:
      partUsage.cache_read_input_tokens !== null &&
      partUsage.cache_read_input_tokens > 0
        ? partUsage.cache_read_input_tokens
        : usage.cache_read_input_tokens,
    output_tokens: partUsage.output_tokens ?? usage.output_tokens,
    server_tool_use: {
      web_search_requests:
        partUsage.server_tool_use?.web_search_requests ??
        usage.server_tool_use.web_search_requests,
      web_fetch_requests:
        partUsage.server_tool_use?.web_fetch_requests ??
        usage.server_tool_use.web_fetch_requests,
    },
    service_tier: usage.service_tier,
    cache_creation: {
      ephemeral_1h_input_tokens:
        (partUsage as BetaUsage).cache_creation?.ephemeral_1h_input_tokens ??
        usage.cache_creation.ephemeral_1h_input_tokens,
      ephemeral_5m_input_tokens:
        (partUsage as BetaUsage).cache_creation?.ephemeral_5m_input_tokens ??
        usage.cache_creation.ephemeral_5m_input_tokens,
    },
    inference_geo: usage.inference_geo,
    iterations: partUsage.iterations ?? usage.iterations,
    speed: (partUsage as BetaUsage).speed ?? usage.speed,
  }
}
```

## Accumulating Usage Across Turns

Claude Code accumulates completed message usage into total session usage:

```ts
export function accumulateUsage(
  totalUsage: Readonly<NonNullableUsage>,
  messageUsage: Readonly<NonNullableUsage>,
): NonNullableUsage {
  return {
    input_tokens: totalUsage.input_tokens + messageUsage.input_tokens,
    cache_creation_input_tokens:
      totalUsage.cache_creation_input_tokens +
      messageUsage.cache_creation_input_tokens,
    cache_read_input_tokens:
      totalUsage.cache_read_input_tokens + messageUsage.cache_read_input_tokens,
    output_tokens: totalUsage.output_tokens + messageUsage.output_tokens,
    server_tool_use: {
      web_search_requests:
        totalUsage.server_tool_use.web_search_requests +
        messageUsage.server_tool_use.web_search_requests,
      web_fetch_requests:
        totalUsage.server_tool_use.web_fetch_requests +
        messageUsage.server_tool_use.web_fetch_requests,
    },
    service_tier: messageUsage.service_tier,
    cache_creation: {
      ephemeral_1h_input_tokens:
        totalUsage.cache_creation.ephemeral_1h_input_tokens +
        messageUsage.cache_creation.ephemeral_1h_input_tokens,
      ephemeral_5m_input_tokens:
        totalUsage.cache_creation.ephemeral_5m_input_tokens +
        messageUsage.cache_creation.ephemeral_5m_input_tokens,
    },
    inference_geo: messageUsage.inference_geo,
    iterations: messageUsage.iterations,
    speed: messageUsage.speed,
  }
}
```

For smart-proxy, the simpler aggregate is enough:

```text
total_input = sum(message.usage.input_tokens)
total_output = sum(message.usage.output_tokens)
total_cache_read = sum(message.usage.cache_read_input_tokens)
total_cache_creation = sum(message.usage.cache_creation_input_tokens)
```

## When Usage Is Written To Transcript

`QueryEngine.ts` shows that assistant messages are persisted to transcript.
For streamed messages, `claude.ts` yields assistant messages and then mutates
the last assistant message's `message.usage` and `stop_reason` when
`message_delta` arrives. Transcript writing is queued/lazy so that mutation can
be captured.

Key behavior from `QueryEngine.ts`:

```ts
if (persistSession) {
  if (message.type === 'assistant') {
    void recordTranscript(messages)
  } else {
    await recordTranscript(messages)
  }
}
```

Usage accumulation in QueryEngine:

```ts
if (message.event.type === 'message_start') {
  currentMessageUsage = EMPTY_USAGE
  currentMessageUsage = updateUsage(
    currentMessageUsage,
    message.event.message.usage,
  )
}
if (message.event.type === 'message_delta') {
  currentMessageUsage = updateUsage(
    currentMessageUsage,
    message.event.usage,
  )
}
if (message.event.type === 'message_stop') {
  this.totalUsage = accumulateUsage(
    this.totalUsage,
    currentMessageUsage,
  )
}
```

Practical implication:

- Recent transcript writes may lag slightly.
- A dashboard should tolerate a small delay and avoid assuming the current
  in-flight response is fully represented until the JSONL line is updated.

## Session Statistics Aggregation

`utils/stats.ts` is the best reference for how Claude Code itself builds stats.
It scans all session files, filters transcript messages, and aggregates model
usage.

Important details:

- It reads JSONL session files.
- It processes files in batches.
- It includes subagent JSONL files for token usage.
- It skips synthetic model usage.
- It groups daily token charts by the first message date of a session.

Core model usage aggregation:

```ts
if (message.message?.usage) {
  const usage = message.message.usage
  const model = message.message.model || 'unknown'

  if (model === SYNTHETIC_MODEL) {
    continue
  }

  modelUsageAgg[model].inputTokens += usage.input_tokens || 0
  modelUsageAgg[model].outputTokens += usage.output_tokens || 0
  modelUsageAgg[model].cacheReadInputTokens +=
    usage.cache_read_input_tokens || 0
  modelUsageAgg[model].cacheCreationInputTokens +=
    usage.cache_creation_input_tokens || 0

  const totalTokens =
    (usage.input_tokens || 0) + (usage.output_tokens || 0)
  if (totalTokens > 0) {
    dayTokens[model] = (dayTokens[model] || 0) + totalTokens
  }
}
```

Note that Claude Code's own "total tokens" display usually means
`inputTokens + outputTokens`. Cache read/write tokens are shown separately in
model details and cost tracking.

## Stats Cache

Claude Code persists an aggregate cache at:

```text
<claude-config-home>/stats-cache.json
```

The cache type includes:

- `version`
- `lastComputedDate`
- `dailyActivity`
- `dailyModelTokens`
- `modelUsage`
- `totalSessions`
- `totalMessages`
- `longestSession`
- `firstSessionDate`
- `hourCounts`
- `totalSpeculationTimeSavedMs`
- optional `shotDistribution`

The current cache version found in the source is:

```ts
export const STATS_CACHE_VERSION = 3
```

Claude Code treats historical days as cacheable and always processes today's
data live:

```ts
// Always process today's data live (it's incomplete)
const todayStats = await processSessionFiles(allSessionFiles, {
  fromDate: today,
  toDate: today,
})

return cacheToStats(updatedCache, todayStats)
```

Practical implication for smart-proxy:

- Option A: read Claude Code's `stats-cache.json` plus today's JSONL files.
- Option B: maintain our own aggregate cache from JSONL files.
- Option C: start with direct JSONL scanning, then optimize once the UI works.

Option B is safest for independence because Claude Code's cache schema may
change. Option A is fast but couples us to a private cache format.

## Cost Tracker

`cost-tracker.ts` receives usage and model after API calls and updates the
session totals.

Relevant behavior:

```ts
modelUsage.inputTokens += usage.input_tokens
modelUsage.outputTokens += usage.output_tokens
modelUsage.cacheReadInputTokens += usage.cache_read_input_tokens ?? 0
modelUsage.cacheCreationInputTokens += usage.cache_creation_input_tokens ?? 0
modelUsage.webSearchRequests +=
  usage.server_tool_use?.web_search_requests ?? 0
```

It also records OpenTelemetry counters for cost and tokens:

```ts
getTokenCounter()?.add(usage.input_tokens, { ...attrs, type: 'input' })
getTokenCounter()?.add(usage.output_tokens, { ...attrs, type: 'output' })
getTokenCounter()?.add(usage.cache_read_input_tokens ?? 0, {
  ...attrs,
  type: 'cacheRead',
})
getTokenCounter()?.add(usage.cache_creation_input_tokens ?? 0, {
  ...attrs,
  type: 'cacheCreation',
})
```

For smart-proxy, local transcript parsing is simpler and less invasive than
hooking telemetry.

## Existing Claude Code Stats UI

`components/Stats.tsx` renders an in-terminal stats UI with:

- Favorite model
- Total tokens
- Sessions
- Longest session
- Activity heatmap
- Model breakdown
- Tokens per day chart
- Date ranges such as all, 7d, and 30d

The UI calculates total tokens as:

```ts
const totalTokens = modelEntries.reduce(
  (sum, [, usage]) => sum + usage.inputTokens + usage.outputTokens,
  0,
)
```

This confirms that cache tokens are not included in the headline total token
count in that UI. They are tracked separately.

## What This Means For Smart-Proxy

Recommended split:

1. smart-proxy request telemetry:
   - total requests
   - successes
   - failures
   - average latency
   - route decision: direct, whitelist direct, upstream proxy
   - host/method/status-ish metadata

2. Claude transcript usage telemetry:
   - input tokens
   - output tokens
   - cache read tokens
   - cache creation tokens
   - daily/weekly/monthly aggregation
   - model breakdown when `message.model` is present
   - service tier and speed if useful

3. Dashboard layer:
   - combine both snapshots by time range
   - show request metrics and token metrics together
   - make clear that request counts and token counts come from different local
     sources and may update at slightly different times

## Caveats

- Claude Code source package is not a stable public API.
- Source-map paths are logical source paths, not checked-out TypeScript files in
  this repository.
- `stats-cache.json` is internal and versioned. Prefer reading JSONL directly
  for correctness unless performance becomes an issue.
- Transcript write timing is asynchronous. Very recent usage may appear a short
  moment after the request completes.
- Provider compatibility depends on Claude Code receiving usage fields from the
  selected Anthropic-compatible endpoint. If a third-party provider omits fields,
  transcript token stats will be partial.
- PLUS/TEAM quota labels and remaining plan limits are not solved by local
  transcript parsing. Those likely require Claude account/billing APIs or a
  separate source of truth.
