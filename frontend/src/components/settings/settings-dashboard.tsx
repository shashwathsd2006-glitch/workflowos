"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import {
  API_BASE_URL,
  ApiError,
  connectIntegration,
  disconnectIntegration,
  getHealth,
  getIntegrationTest,
  getIntegrations,
  getSchedulerStatus,
  getSystemStatus,
  getWorkerStatus,
  testIntegration,
} from "@/lib/api";
import { IconDatabase, IconShield } from "@/components/icons";
import type {
  GmailDiagnosis,
  IntegrationStatus,
  SchedulerStatus,
  SystemStatus,
  WorkerStatus,
} from "@/types/api";

type Dot = "ok" | "bad" | "idle";

const DOT_STYLES: Record<Dot, string> = {
  ok: "bg-emerald-400 ring-emerald-400/25",
  bad: "bg-rose-400 ring-rose-400/25",
  idle: "bg-zinc-500 ring-zinc-500/20",
};

function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString([], {
    hour12: false,
  })}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.detail) return error.detail;
    return error.status === 0
      ? "Backend unreachable — is the API running?"
      : `Backend request failed (HTTP ${error.status})`;
  }
  return error instanceof Error ? error.message : "Something went wrong";
}

function Row({
  label,
  value,
  mono = true,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-white/[0.04] py-2 last:border-0">
      <dt className="text-xs text-zinc-500">{label}</dt>
      <dd
        className={`truncate text-right text-xs text-zinc-300 ${mono ? "font-mono" : ""}`}
      >
        {value}
      </dd>
    </div>
  );
}

/** Human label for each of the six Gmail connection states. */
function gmailStateLabel(state: string): string {
  switch (state) {
    case "not_configured":
      return "Not configured";
    case "configured_not_connected":
      return "Not connected";
    case "connected":
      return "Connected";
    case "token_expired_refreshable":
      return "Token expired — refresh available";
    case "permission_denied":
      return "Connection error — permission denied";
    case "authentication_error":
      return "Connection error — authorisation rejected";
    default:
      return "Connection error";
  }
}

/** Map the backend OAuth callback query string to a user-facing message. */
function readOAuthCallback(): string | null {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  const outcome = params.get("gmail");
  if (!outcome) return null;
  const detail = params.get("detail") ?? "";
  switch (outcome) {
    case "connected":
      return `Gmail connected — verified with the Gmail API. ${detail}`;
    case "profile_failed":
      return `Gmail authorised but the API call failed: ${detail}`;
    case "cancelled":
      return "Gmail connection cancelled — nothing was connected.";
    case "invalid_state":
      return `Gmail connection refused: ${detail || "the security token did not match."} Press Connect Gmail again.`;
    case "missing_code":
      return "Gmail connection failed: Google did not return a code.";
    case "exchange_failed":
      return `Gmail connection failed: ${detail || "the code was rejected."}`;
    case "redirect_uri_mismatch":
      return `Gmail connection failed: ${detail}`;
    case "denied":
      return `Gmail connection failed: ${detail || "Google refused the request."}`;
    case "unavailable":
      return `Gmail connection failed: ${detail || "Google was unreachable."}`;
    case "not_configured":
      return "Gmail is not configured on this server.";
    case "storage_failed":
      return "Gmail connected but the token could not be stored.";
    default:
      return `Gmail: ${detail || outcome}`;
  }
}

/**
 * Subscribing is a no-op: the query string only changes through a real browser
 * navigation, which produces a fresh page render anyway.
 */
function subscribeToNothing(): () => void {
  return () => {};
}

/** Browser snapshot: the real OAuth outcome carried in the URL. */
function getOauthBannerSnapshot(): string | null {
  return readOAuthCallback();
}

/** Server snapshot: nothing. Guarantees an identical first client render. */
function getOauthBannerServerSnapshot(): string | null {
  return null;
}

function Indicator({ label, state, value }: { label: string; state: Dot; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2.5">
      <span className="flex items-center gap-2 text-sm text-zinc-300">
        <span className={`h-2 w-2 rounded-full ring-4 ${DOT_STYLES[state]}`} />
        {label}
      </span>
      <span className="truncate font-mono text-[11px] text-zinc-500">{value}</span>
    </div>
  );
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] p-5">
      <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.16em] text-zinc-500">
        {icon}
        {title}
      </h2>
      {children}
    </section>
  );
}

export function SettingsDashboard() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [health, setHealth] = useState<Dot>("idle");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [testing, setTesting] = useState(false);
  const [testedAt, setTestedAt] = useState<string | null>(null);
  const [integrations, setIntegrations] = useState<IntegrationStatus[]>([]);
  const [scheduler, setScheduler] = useState<SchedulerStatus | null>(null);
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [busyProvider, setBusyProvider] = useState<string | null>(null);
  const [runtimeNotice, setRuntimeNotice] = useState("");
  const [pendingAuthorize, setPendingAuthorize] = useState<string | null>(null);
  const [gmailDiagnosis, setGmailDiagnosis] = useState<GmailDiagnosis | null>(null);

  // The OAuth callback returns to /settings?gmail=<outcome>&detail=<reason>.
  // `useSyncExternalStore` is the SSR-safe way to read that: React renders the
  // server snapshot (null) during SSR *and* hydration, then re-renders with the
  // real browser value. Reading `window` during render instead made the server
  // emit no banner while the browser emitted one — a text mismatch.
  const oauthBanner = useSyncExternalStore(
    subscribeToNothing,
    getOauthBannerSnapshot,
    getOauthBannerServerSnapshot,
  );

  const load = useCallback((): Promise<void> => {
    return Promise.all([getSystemStatus(), getHealth()]).then(
      ([statusPayload]) => {
        setStatus(statusPayload);
        setHealth("ok");
        setError("");
      },
      (failure: unknown) => {
        setHealth("bad");
        setError(errorMessage(failure));
      },
    );
  }, []);

  const loadRuntime = useCallback((): Promise<void> => {
    return Promise.all([
      getIntegrations(),
      getSchedulerStatus(),
      getWorkerStatus(),
    ]).then(
      ([integrationList, schedulerStatus, workerStatus]) => {
        setIntegrations(integrationList.integrations);
        setScheduler(schedulerStatus);
        setWorker(workerStatus);
      },
      () => {
        // Runtime panels degrade independently; the page still renders.
      },
    );
  }, []);

  useEffect(() => {
    load()
      .then(loadRuntime)
      .then(() => setLoading(false));
  }, [load, loadRuntime]);

  const runGmailTest = async () => {
    setBusyProvider("gmail");
    setError("");
    setRuntimeNotice("");
    try {
      const result = await getIntegrationTest("gmail");
      setGmailDiagnosis(result);
      setRuntimeNotice(
        `Gmail: ${result.state.replace(/_/g, " ")} — ${result.message}`,
      );
      await loadRuntime();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusyProvider(null);
    }
  };

  const integrationAction = async (
    provider: string,
    action: "connect" | "disconnect" | "test",
  ) => {
    setBusyProvider(provider);
    setError("");
    setRuntimeNotice("");
    try {
      if (action === "connect") {
        const result = await connectIntegration(provider, {});
        if (result.authorize_url) {
          // Real OAuth needs a full browser navigation to Google's consent
          // screen. Navigate immediately so one click does the whole job; the
          // anchor stays as a visible fallback in case navigation is blocked.
          setPendingAuthorize(result.authorize_url);
          window.location.assign(result.authorize_url);
          return;
        }
        setRuntimeNotice(`${provider}: ${result.message}`);
      } else if (action === "disconnect") {
        await disconnectIntegration(provider);
        setRuntimeNotice(`${provider} disconnected.`);
      } else {
        const result = await testIntegration(provider);
        setRuntimeNotice(
          `${provider}: ${result.ok ? "OK" : "not ready"} — ${result.message}`,
        );
      }
      await loadRuntime();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusyProvider(null);
    }
  };

  const testOllama = async () => {
    setTesting(true);
    setError("");
    try {
      setStatus(await getSystemStatus());
      setTestedAt(new Date().toISOString());
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setTesting(false);
    }
  };

  const ollama = status?.ai.ollama;
  const ollamaState: Dot = !ollama
    ? "idle"
    : ollama.status === "ok" && ollama.configured_model_present
      ? "ok"
      : ollama.status === "ok"
        ? "bad"
        : "bad";
  const dbState: Dot = !status
    ? "idle"
    : status.database.status === "ok"
      ? "ok"
      : "bad";

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-white">
          Settings
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          Read-only system configuration and health. Values come from the
          backend and environment — nothing here is a fake toggle.
        </p>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-400/25 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {oauthBanner && (
        <div
          className={
            oauthBanner.startsWith("Gmail connected")
              ? "rounded-xl border border-emerald-400/25 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300"
              : "rounded-xl border border-amber-400/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-300"
          }
        >
          {oauthBanner}
        </div>
      )}

      {pendingAuthorize && (
        <a
          href={pendingAuthorize}
          className="block rounded-xl border border-brand-400/40 bg-brand-500/10 px-4 py-3 text-sm text-brand-200 transition-colors hover:bg-brand-500/20"
        >
          Continue to Google sign-in &rarr;
          <span className="ml-2 text-xs text-brand-300/70">
            (authorise WorkFlowOS to access your Gmail)
          </span>
        </a>
      )}

      {loading ? (
        <div className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] px-4 py-16 text-center text-sm text-zinc-500">
          Loading system status…
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Section title="General">
            <dl>
              <Row
                label="Application"
                value={status?.application.name ?? "—"}
                mono={false}
              />
              <Row label="API version" value={status?.application.version ?? "—"} />
              <Row
                label="Environment"
                value={status?.application.environment ?? "—"}
              />
              <Row
                label="Debug logging"
                value={status?.application.debug ? "enabled" : "disabled"}
              />
              <Row
                label="Similarity threshold"
                value={String(status?.discovery.similarity_threshold ?? "—")}
              />
            </dl>
            <p className="mt-3 text-[11px] leading-relaxed text-zinc-600">
              The similarity threshold is set by the
              <span className="font-mono"> SIMILARITY_THRESHOLD </span>
              environment variable and controls repetition detection.
            </p>
          </Section>

          <Section title="AI Provider">
            <dl>
              <Row label="Provider" value={status?.ai.provider ?? "—"} />
              <Row label="Ollama endpoint" value={ollama?.endpoint ?? "—"} />
              <Row label="Configured model" value={ollama?.model ?? "—"} />
              <Row
                label="Timeout"
                value={`${ollama?.timeout_seconds ?? "—"}s`}
              />
              <Row
                label="Connection"
                value={ollama ? ollama.status : "unknown"}
              />
              <Row
                label="Model available"
                value={ollama ? (ollama.configured_model_present ? "yes" : "no") : "—"}
              />
            </dl>
            <div className="mt-4 flex items-center gap-3">
              <button
                type="button"
                onClick={() => void testOllama()}
                disabled={testing}
                className="rounded-lg border border-white/[0.1] px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:border-brand-400/40 hover:text-brand-200 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {testing ? "Testing…" : "Test Connection"}
              </button>
              {testedAt && (
                <span className="text-[11px] text-zinc-600">
                  checked {formatDateTime(testedAt)}
                </span>
              )}
            </div>
            {ollama && ollama.models.length > 0 && (
              <p className="mt-3 text-[11px] text-zinc-600">
                Local models: {ollama.models.join(", ")}
              </p>
            )}
          </Section>

          <Section title="Backend">
            <dl>
              <Row label="Base URL" value={API_BASE_URL} />
              <Row
                label="Bound to"
                value={
                  status
                    ? `${status.server.host}:${status.server.port}`
                    : "—"
                }
              />
              <Row label="Health endpoint" value={status?.server.health_endpoint ?? "—"} />
              <Row
                label="Readiness"
                value={status?.server.readiness_endpoint ?? "—"}
              />
              <Row
                label="Health"
                value={
                  health === "ok"
                    ? "ok"
                    : health === "bad"
                      ? "unreachable"
                      : "unknown"
                }
              />
            </dl>
          </Section>

          <Section title="Database" icon={<IconDatabase className="h-4 w-4" />}>
            <dl>
              <Row label="Engine" value={status?.database.engine ?? "—"} />
              <Row label="File" value={status?.database.name ?? "—"} />
              <Row
                label="Path"
                value={status?.database.path ?? "—"}
              />
              <Row
                label="File exists"
                value={status ? (status.database.exists ? "yes" : "no") : "—"}
              />
              <Row
                label="Status"
                value={status?.database.status ?? "—"}
              />
            </dl>
          </Section>

          <Section title="Safety" icon={<IconShield className="h-4 w-4" />}>
            <dl>
              <Row
                label="Approval required"
                value={status?.execution.approval_required ? "yes" : "no"}
              />
              <Row
                label="External integrations"
                value={
                  status && status.execution.external_integrations.length === 0
                    ? "none connected"
                    : (status?.execution.external_integrations.join(", ") ?? "—")
                }
              />
            </dl>
            <p className="mt-3 text-[11px] leading-relaxed text-zinc-500">
              {status?.execution.note}
            </p>
            <div className="mt-3">
              <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                Available action model
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {(status?.execution.allowed_actions ?? []).map((action) => (
                  <span
                    key={action}
                    className="rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 font-mono text-[11px] text-zinc-400"
                  >
                    {action}
                  </span>
                ))}
              </div>
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-zinc-600">
              These are the only actions the execution engine may run. A
              generated step with any other action fails safely.
            </p>
          </Section>

          <Section title="Integrations">
            <div className="space-y-3">
              {integrations.length === 0 ? (
                <p className="text-sm text-zinc-600">
                  Integration status unavailable.
                </p>
              ) : (
                integrations.map((integration) => {
                  const state: Dot =
                    integration.state === "connected"
                      ? "ok"
                      : integration.state === "not_configured"
                        ? "bad"
                        : integration.state === "error"
                          ? "bad"
                          : "idle";
                  return (
                    <div
                      key={integration.provider}
                      className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="flex items-center gap-2 text-sm text-white">
                            <span
                              className={`h-2 w-2 rounded-full ring-4 ${DOT_STYLES[state]}`}
                            />
                            {integration.label}
                            {integration.is_mock && (
                              <span className="rounded-md border border-amber-400/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-amber-300">
                                Demo
                              </span>
                            )}
                          </p>
                          <p className="mt-1 text-[11px] leading-relaxed text-zinc-500">
                            {gmailDiagnosis &&
                            integration.provider === "gmail" &&
                            !integration.is_mock
                              ? gmailDiagnosis.message
                              : integration.message}
                          </p>

                          {integration.provider === "gmail" &&
                            !integration.is_mock && (
                              <p className="mt-1 text-[11px] font-medium text-zinc-300">
                                {gmailDiagnosis
                                  ? gmailStateLabel(gmailDiagnosis.state)
                                  : integration.state === "connected"
                                    ? "Connected"
                                    : integration.state === "not_configured"
                                      ? "Not configured"
                                      : "Not connected"}
                              </p>
                            )}
                          {integration.provider === "gmail" &&
                            !integration.is_mock && (
                              <p
                                className={`mt-1 text-[11px] font-medium ${
                                  integration.connected
                                    ? "text-emerald-300"
                                    : "text-amber-300/90"
                                }`}
                              >
                                {integration.connected
                                  ? "Gmail connected"
                                  : "Gmail not connected"}
                              </p>
                            )}
                          <p className="mt-0.5 font-mono text-[10px] text-zinc-600">
                            {integration.provider}
                            {integration.events_seen
                              ? ` · ${integration.events_seen} event(s) seen`
                              : ""}
                            {integration.credential_source
                              ? ` · credentials: ${integration.credential_source}`
                              : ""}
                          </p>

                          {integration.connected && integration.account_email && (
                            <p className="mt-1 text-[11px] text-emerald-300">
                              Connected account: {integration.account_email}
                            </p>
                          )}

                          {!integration.is_mock &&
                            integration.requested_scopes &&
                            integration.requested_scopes.length > 0 && (
                              <p className="mt-1.5 text-[10px] text-zinc-600">
                                Scopes requested:{" "}
                                <span className="font-mono">
                                  {integration.requested_scopes
                                    .map((scope) => scope.split("/").pop())
                                    .join(", ")}
                                </span>
                              </p>
                            )}

                          {!integration.is_mock && integration.redirect_uri && (
                            <div className="mt-2 rounded-lg border border-white/[0.07] bg-black/30 px-2.5 py-2">
                              <p className="text-[10px] uppercase tracking-[0.14em] text-zinc-500">
                                Authorized redirect URI (must match Google Cloud exactly)
                              </p>
                              <p className="mt-0.5 select-all break-all font-mono text-[11px] text-zinc-300">
                                {integration.redirect_uri}
                              </p>
                            </div>
                          )}
                        </div>
                        <span className="shrink-0 rounded-md border border-white/[0.1] bg-white/[0.03] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-zinc-400">
                          {integration.state.replace(/_/g, " ")}
                        </span>
                      </div>

                      <div className="mt-3 flex flex-wrap gap-2">
                        {integration.state !== "not_configured" && (
                          <button
                            type="button"
                            disabled={busyProvider === integration.provider}
                            onClick={() =>
                              void integrationAction(
                                integration.provider,
                                "connect",
                              )
                            }
                            className="rounded-lg border border-white/[0.1] px-2.5 py-1 text-[11px] text-zinc-300 transition-colors hover:border-brand-400/40 hover:text-brand-200 disabled:opacity-60"
                          >
                            {integration.is_mock
                              ? "Connect demo"
                              : `Connect ${integration.label} (Google OAuth)`}
                          </button>
                        )}
                        <button
                          type="button"
                          disabled={busyProvider === integration.provider}
                          onClick={() =>
                            void integrationAction(
                              integration.provider,
                              "disconnect",
                            )
                          }
                          className="rounded-lg border border-white/[0.1] px-2.5 py-1 text-[11px] text-zinc-400 transition-colors hover:border-rose-400/40 hover:text-rose-300 disabled:opacity-60"
                        >
                          Disconnect
                        </button>
                        <button
                          type="button"
                          disabled={busyProvider === integration.provider}
                          onClick={() =>
                            integration.provider === "gmail" &&
                            !integration.is_mock
                              ? void runGmailTest()
                              : void integrationAction(
                                  integration.provider,
                                  "test",
                                )
                          }
                          className="rounded-lg border border-white/[0.1] px-2.5 py-1 text-[11px] text-zinc-300 transition-colors hover:border-white/[0.25] disabled:opacity-60"
                        >
                          Test Connection
                        </button>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
            {runtimeNotice && (
              <p className="mt-3 rounded-lg border border-white/[0.07] bg-black/20 px-3 py-2 text-[11px] text-zinc-400">
                {runtimeNotice}
              </p>
            )}
            <p className="mt-3 text-[11px] leading-relaxed text-zinc-600">
              OAuth tokens are encrypted at rest and are never returned by this
              API. A provider without credentials reports &ldquo;not
              configured&rdquo; — it is never shown as connected.
            </p>
          </Section>

          <Section title="OAuth Diagnostics">
            {status?.oauth_diagnostics ? (
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2 text-[11px]">
                  <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
                    <p className="text-zinc-500">OAuth configured</p>
                    <p className="mt-0.5 font-mono text-zinc-200">
                      {status.oauth_diagnostics.oauth_configured ? "YES" : "NO"}
                    </p>
                  </div>
                  <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
                    <p className="text-zinc-500">OAuth source</p>
                    <p className="mt-0.5 font-mono text-zinc-200">
                      {status.oauth_diagnostics.oauth_source}
                    </p>
                  </div>
                </div>
                {status.oauth_diagnostics.redirect_uri && (
                  <p className="break-all font-mono text-[10px] text-zinc-600">
                    redirect_uri: {status.oauth_diagnostics.redirect_uri}
                  </p>
                )}
                <div className="mt-2 space-y-1.5">
                  {status.oauth_diagnostics.providers.map((entry) => (
                    <div
                      key={entry.provider}
                      className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-[11px] text-zinc-300">
                          {entry.provider}
                          {entry.is_mock && (
                            <span className="ml-2 text-amber-300">demo</span>
                          )}
                        </span>
                        <span className="font-mono text-[10px] text-zinc-500">
                          {entry.state ?? "—"}
                        </span>
                      </div>
                      <p className="mt-0.5 text-[10px] text-zinc-600">
                        OAuth configured:{" "}
                        {entry.configured ? "YES" : "NO"} · connected:{" "}
                        {entry.connected ? "YES" : "NO"} · token refresh:{" "}
                        {entry.refresh_capability ?? "unavailable"} · last test:{" "}
                        {formatDateTime(entry.last_test)}
                      </p>
                      {entry.account_email && (
                        <p className="mt-0.5 font-mono text-[10px] text-emerald-300">
                          {entry.account_email}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
                <p className="mt-2 text-[10px] leading-relaxed text-zinc-700">
                  Booleans and provenance only. No client secret, access token
                  or refresh token is exposed by this page.
                </p>
              </div>
            ) : (
              <p className="text-sm text-zinc-600">
                OAuth diagnostics unavailable.
              </p>
            )}
          </Section>

          <Section title="Scheduler">
            <div className="space-y-2">
              <Indicator
                label="Scheduler"
                state={scheduler?.running ? "ok" : "bad"}
                value={scheduler?.running ? "running" : "stopped"}
              />
              <div className="grid grid-cols-2 gap-2 text-[11px]">
                <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
                  <p className="text-zinc-500">Active schedules</p>
                  <p className="mt-0.5 font-mono text-zinc-200">
                    {scheduler?.active_schedules ?? "—"}
                  </p>
                </div>
                <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
                  <p className="text-zinc-500">Next scheduled job</p>
                  <p className="mt-0.5 font-mono text-zinc-200">
                    {formatDateTime(scheduler?.next_due)}
                  </p>
                </div>
                <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
                  <p className="text-zinc-500">Ticks</p>
                  <p className="mt-0.5 font-mono text-zinc-200">
                    {scheduler?.ticks ?? "—"}
                  </p>
                </div>
                <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
                  <p className="text-zinc-500">Jobs enqueued</p>
                  <p className="mt-0.5 font-mono text-zinc-200">
                    {scheduler?.jobs_enqueued ?? "—"}
                  </p>
                </div>
              </div>
              {scheduler && !scheduler.running && (
                <p className="text-[11px] text-amber-300">
                  The scheduler is not running. Set SCHEDULER_ENABLED=true and
                  restart the backend.
                </p>
              )}
            </div>
          </Section>

          <Section title="Worker">
            <div className="space-y-2">
              <Indicator
                label="Background worker"
                state={worker?.running ? "ok" : "bad"}
                value={worker?.running ? "running" : "stopped"}
              />
              <div className="grid grid-cols-2 gap-2 text-[11px]">
                <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
                  <p className="text-zinc-500">Queue size</p>
                  <p className="mt-0.5 font-mono text-zinc-200">
                    {worker
                      ? worker.queue.queued + worker.queue.running
                      : "—"}
                  </p>
                </div>
                <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
                  <p className="text-zinc-500">Jobs processed</p>
                  <p className="mt-0.5 font-mono text-zinc-200">
                    {worker?.jobs_processed ?? "—"}
                  </p>
                </div>
                <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
                  <p className="text-zinc-500">Queued / running</p>
                  <p className="mt-0.5 font-mono text-zinc-200">
                    {worker ? `${worker.queue.queued} / ${worker.queue.running}` : "—"}
                  </p>
                </div>
                <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
                  <p className="text-zinc-500">Last processed job</p>
                  <p className="mt-0.5 font-mono text-zinc-200">
                    {worker?.last_processed_job_id ?? "—"}
                  </p>
                </div>
              </div>
              {worker && worker.queue.failed > 0 && (
                <p className="text-[11px] text-rose-300">
                  {worker.queue.failed} job(s) failed. See Analytics for the
                  reason and whether a retry is pending.
                </p>
              )}
            </div>
          </Section>

          <Section title="System">
            <div className="space-y-2">
              <Indicator
                label="Frontend"
                state="ok"
                value="Next.js · this page"
              />
              <Indicator
                label="Backend API"
                state={health}
                value={API_BASE_URL}
              />
              <Indicator
                label="Database"
                state={dbState}
                value={status?.database.engine ?? "unknown"}
              />
              <Indicator
                label="Ollama"
                state={ollamaState}
                value={ollama ? `${ollama.status} · ${ollama.model}` : "unknown"}
              />
            </div>
            <p className="mt-4 text-[11px] leading-relaxed text-zinc-600">
              No credentials, tokens or secrets are exposed by this page. It
              reports configuration and reachability only.
            </p>
          </Section>

        </div>
      )}
    </div>
  );
}
