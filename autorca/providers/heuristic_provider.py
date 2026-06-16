"""Offline, rule-based analysis provider.

Requires no API key and no internet. It recognises common error *signatures*
(auth/JWT, NPE, DB/connection, timeout, config, OOM, etc.) and produces a
best-effort root-cause analysis. It is also used as the automatic fallback when
the AI provider is unavailable, so the service never silently drops a file.
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional

from ..config import Config
from ..log_parser import ErrorDigest
from .base import AnalysisProvider, AnalysisResult


class _Signature:
    def __init__(
        self,
        name: str,
        pattern: str,
        category: str,
        level: str,
        error_type: str,
        simple: str,
        why: str,
        impact: str,
        root_cause: str,
        steps: List[str],
        trigger: str,
    ):
        self.name = name
        self.regex = re.compile(pattern, re.I)
        self.category = category
        self.level = level                # Application | Server | Database | Network
        self.error_type = error_type
        self.simple = simple              # plain, non-technical explanation
        self.why = why
        self.impact = impact
        self.root_cause = root_cause
        self.steps = steps
        self.trigger = trigger


# Order matters: more specific signatures first.
_SIGNATURES: List[_Signature] = [
    _Signature(
        name="jwt_expired",
        pattern=r"ExpiredJwtException|JWT expired|token (?:is )?expired|InvalidJwtToken",
        category="integration",
        level="Application",
        error_type="Authentication / Session Expiry",
        simple="The user's login session had expired, so the system blocked the request. "
               "The user simply needs to log in again to get a fresh session.",
        why="The JSON Web Token presented with the request had a timestamp (exp) "
            "in the past, so token validation rejected it before the request could be routed.",
        impact="Affected requests are rejected with HTTP 401 (Unauthorized). End users "
               "see failures / are forced to re-authenticate; downstream services are not reached.",
        root_cause="An expired or stale auth token reached the gateway. Typically the client "
                   "did not refresh the token in time, the token TTL is too short, or client/server "
                   "clocks are skewed.",
        steps=[
            "Have the client refresh the access token (use the refresh-token flow) before retrying.",
            "Review the token TTL (exp lifetime) — increase it or implement silent refresh if it is too aggressive.",
            "Verify server and client clocks are NTP-synchronised; clock skew can expire tokens early.",
            "Ensure the gateway returns a clear 401 'token expired' code the client can act on (it does here: error.code 30_0002_004).",
        ],
        trigger="A request arrived carrying a JWT whose expiry time had already passed.",
    ),
    _Signature(
        name="null_pointer",
        pattern=r"NullPointerException",
        category="application",
        level="Application",
        error_type="Application Defect (NullPointerException)",
        simple="The program tried to use something that wasn't there (an empty value), "
               "and crashed on that line. This is a coding bug that needs a fix in the app.",
        why="Code dereferenced an object reference that was null at runtime.",
        impact="The operation in that code path fails. If it is in a logging/observability "
               "filter, it may not break the user response but it corrupts logs/telemetry "
               "and hides other failures.",
        root_cause="A value assumed to be present was null — commonly a missing response/stream, "
                   "an unchecked map lookup, or an uninitialised field.",
        steps=[
            "Open the class & line shown in the top application stack frame and add a null guard.",
            "Trace where the null value originates; fix the source rather than only the symptom.",
            "Add a regression test reproducing the null input.",
        ],
        trigger="A code path received a null where a non-null object was expected.",
    ),
    _Signature(
        name="db_connection",
        pattern=r"(SQLException|CannotGetJdbcConnection|Connection refused|"
                r"Communications link failure|PSQLException|deadlock|connection pool|"
                r"could not connect|timeout.*connect)",
        category="infrastructure",
        level="Database",
        error_type="Database / Connectivity Failure",
        simple="The app couldn't reach or use its database, so anything needing data failed. "
               "Usually the database is down, unreachable, or overloaded.",
        why="The application could not establish or use a connection to a backing datastore/service.",
        impact="Requests depending on that datastore fail or hang. Can cascade into pool exhaustion "
               "and widespread 5xx errors.",
        root_cause="The datastore is unreachable, overloaded, or misconfigured (wrong host/port/"
                   "credentials), or the connection pool is exhausted.",
        steps=[
            "Verify the datastore is up and reachable from the app host (network/firewall/DNS).",
            "Check connection-pool sizing and for leaked/unclosed connections.",
            "Confirm credentials and connection string/config are correct for this environment.",
            "Inspect datastore-side load, locks, and slow queries.",
        ],
        trigger="An attempt to acquire or use a datastore connection failed.",
    ),
    _Signature(
        name="timeout",
        pattern=r"(TimeoutException|Read timed out|SocketTimeout|request timed out|"
                r"ConnectTimeout|gateway timeout|504)",
        category="integration",
        level="Network",
        error_type="Timeout / Slow Dependency",
        simple="A call to another service took too long and gave up. The other service is "
               "slow or unreachable over the network.",
        why="A call to a downstream/upstream dependency did not complete within the allotted time.",
        impact="Requests are delayed or fail; thread pools can fill, degrading the whole service.",
        root_cause="A dependency is slow or unresponsive, or the configured timeout is too tight "
                   "for normal latency.",
        steps=[
            "Identify the slow dependency from the stack/trace id and check its health & latency.",
            "Add/adjust timeouts, retries with backoff, and a circuit breaker.",
            "Scale or fix the slow dependency if latency is systemic.",
        ],
        trigger="A downstream call exceeded its timeout window.",
    ),
    _Signature(
        name="oom",
        pattern=r"(OutOfMemoryError|GC overhead limit|heap space|Metaspace)",
        category="infrastructure",
        level="Server",
        error_type="Resource Exhaustion (Out of Memory)",
        simple="The server ran out of memory and became unstable. It needs more memory or "
               "a fix for whatever is using too much.",
        why="The JVM could not allocate memory; the heap (or metaspace) was exhausted.",
        impact="The process becomes unstable and may crash, dropping all in-flight requests.",
        root_cause="A memory leak, undersized heap, or an unbounded in-memory data structure.",
        steps=[
            "Capture and analyse a heap dump to find the dominant retained objects.",
            "Right-size -Xmx / container memory limits for the workload.",
            "Fix leaks (caches without eviction, growing collections, unclosed resources).",
        ],
        trigger="A memory allocation failed because available memory was exhausted.",
    ),
    _Signature(
        name="config",
        pattern=r"(BeanCreationException|NoSuchBeanDefinition|could not resolve placeholder|"
                r"FileNotFoundException|ClassNotFoundException|NoClassDefFoundError|"
                r"property.*not found|misconfigur)",
        category="configuration",
        level="Server",
        error_type="Configuration / Wiring Error",
        simple="Something the app needs to start or run was missing or set up wrong "
               "(a setting, file, or component). It's a setup/configuration problem.",
        why="A required configuration value, class, or bean was missing or could not be resolved.",
        impact="The affected feature or, at startup, the whole application fails to initialise.",
        root_cause="Missing/incorrect config property, absent dependency on the classpath, or a "
                   "wiring mistake between components.",
        steps=[
            "Check the named property/bean/class referenced in the error against this environment's config.",
            "Confirm all required dependencies are present and versions are compatible.",
            "Compare config with a known-good environment to spot the missing/changed value.",
        ],
        trigger="The application tried to use a config value or component that was not available.",
    ),
]


class HeuristicProvider(AnalysisProvider):
    name = "heuristic"

    def __init__(self, config: Optional[Config] = None):
        self.config = config

    def analyze(self, digest: ErrorDigest, file_name: str) -> AnalysisResult:
        haystack = "\n".join(
            digest.error_lines + digest.caused_by_chain + digest.exception_classes
        )

        matched = [s for s in _SIGNATURES if s.regex.search(haystack)]
        primary = matched[0] if matched else None

        component = digest.components[0] if digest.components else "Unknown component"
        exc = digest.primary_exception or "Unknown"
        failure_point = self._failure_point(digest)

        if primary:
            result = AnalysisResult(
                summary=f"{primary.error_type}: {exc.rsplit('.', 1)[-1]} in {component}.",
                simple_explanation=primary.simple,
                what_happened=self._what_happened(digest, exc),
                why_it_happened=primary.why,
                impact=primary.impact,
                root_cause=primary.root_cause,
                resolution_steps=list(primary.steps),
                level=primary.level,
                level_reason=f"The fault originates in the {primary.level.lower()} layer.",
                error_type=primary.error_type,
                category=primary.category,
                probable_trigger=primary.trigger,
                confidence="High" if len(matched) == 1 else "Medium",
                confidence_reason=(
                    "Matched a known error signature with a clear caused-by chain."
                    if digest.caused_by_chain else
                    "Matched a known error signature."
                ),
            )
        else:
            result = AnalysisResult(
                summary=f"Unclassified error involving {exc.rsplit('.', 1)[-1]} in {component}.",
                simple_explanation="The app hit an error we couldn't match to a known pattern. "
                                   "Someone should look at the stack trace to confirm the cause.",
                what_happened=self._what_happened(digest, exc),
                why_it_happened="The log shows an error/exception but it does not match a known "
                                "signature; manual review of the stack trace is recommended.",
                impact="At least one request/operation failed. Severity depends on how often this recurs.",
                root_cause=f"Probable root cause: {digest.caused_by_chain[-1] if digest.caused_by_chain else exc}.",
                resolution_steps=[
                    "Open the topmost application stack frame and inspect the failing code path.",
                    "Correlate by the trace/correlation id to see the full request lifecycle.",
                    "Reproduce with the same input to confirm and then patch.",
                ],
                level="Application",
                level_reason="Defaulted to application level; no infrastructure signature matched.",
                error_type="Unclassified Error",
                category="application",
                probable_trigger="See the first error line in the digest.",
                confidence="Low",
                confidence_reason="No known signature matched; analysis is generic.",
            )

        # Secondary / cascading failures: any additional matched signatures.
        result.cascading_failures = [
            f"{s.error_type} (signature: {s.name})" for s in matched[1:]
        ]
        result.exception_class = exc
        result.affected_component = component
        result.failure_point = failure_point
        result.sequence_of_events = self._sequence(digest)
        result.reason_groups = self._build_reason_groups(digest)
        result.engine = self.name
        return result

    def _build_reason_groups(self, digest: ErrorDigest) -> List[dict]:
        """One explanation per distinct error reason across all affected endpoints."""
        groups: List[dict] = []
        seen: set = set()
        for inc in digest.incidents:
            if inc.reason in seen:
                continue
            seen.add(inc.reason)
            sig = next((s for s in _SIGNATURES if s.regex.search(inc.reason)), None)
            if sig:
                groups.append({
                    "reason": inc.reason, "title": sig.error_type, "level": sig.level,
                    "simple_explanation": sig.simple, "what_happened": sig.why,
                    "root_cause": sig.root_cause, "fix": sig.steps[0] if sig.steps else "",
                })
            else:
                sev = "Server" if inc.status[:1] == "5" else "Application"
                groups.append({
                    "reason": inc.reason,
                    "title": f"HTTP {inc.status} on {inc.path.rsplit('/', 1)[-1] or inc.path}",
                    "level": sev,
                    "simple_explanation": f"The endpoint returned {inc.status} with reason "
                                          f"'{inc.reason}'. Review this call's handler and inputs.",
                    "what_happened": f"{inc.endpoint} responded {inc.status}.",
                    "root_cause": f"Reason code/message: {inc.reason}.",
                    "fix": "Inspect the handler for this endpoint and the downstream it calls.",
                })
            if len(groups) >= 8:
                break
        return groups

    @staticmethod
    def _what_happened(digest: ErrorDigest, exc: str) -> str:
        bits = []
        if digest.http_events:
            bits.append(f"While handling {digest.http_events[0]}, ")
        bits.append(f"the service raised {exc.rsplit('.', 1)[-1]}")
        if digest.caused_by_chain:
            bits.append(f", caused by: {digest.caused_by_chain[-1][:200]}")
        bits.append(".")
        return "".join(bits)

    @staticmethod
    def _failure_point(digest: ErrorDigest) -> str:
        # First application stack frame in the error lines, if any.
        for line in digest.error_lines:
            m = re.search(r"at\s+([\w$.]+\([\w.]+:\d+\))", line)
            if m and not m.group(1).startswith((
                "org.springframework", "com.netflix", "io.undertow", "java.", "javax.")):
                return m.group(1)
        return digest.components[0] if digest.components else "Unknown"

    @staticmethod
    def _sequence(digest: ErrorDigest) -> List[str]:
        seq: List[str] = []
        if digest.http_events:
            seq.append(f"Request received: {digest.http_events[0]}")
        # Dedupe repeated causes (the same exception often recurs in the log).
        seen: set = set()
        for cause in reversed(digest.caused_by_chain):
            exc = cause.split(":", 1)[0].strip()
            if exc in seen:
                continue
            seen.add(exc)
            seq.append(f"Triggered: {cause[:160]}")
        res = [e for e in digest.http_events if "-> " in e]
        if res:
            seq.append(f"Response returned: {res[-1]}")
        return seq or ["Single error event detected; insufficient sequence context."]
