"use client";

/**
 * Choose which real Gmail message a workflow run should process.
 *
 * The list comes straight from the authenticated account's mailbox through
 * `GET /api/integrations/gmail/messages` — no mock data, and no token ever
 * reaches the browser. The selected `message_id` is passed into the execution
 * request so step 1 reads exactly that message.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { ApiError, listGmailMessages } from "@/lib/api";
import type { GmailMessageList, GmailMessageSummary } from "@/types/api";

interface EmailSelectionValue {
  messages: GmailMessageSummary[];
  selected: GmailMessageSummary | null;
  loading: boolean;
  error: string;
  connected: boolean;
  select: (message: GmailMessageSummary | null) => void;
  refresh: () => Promise<void>;
}

const EmailSelectionContext = createContext<EmailSelectionValue | null>(null);

function describe(failure: unknown): string {
  if (failure instanceof ApiError) {
    return failure.detail ?? failure.message;
  }
  return failure instanceof Error ? failure.message : "Something went wrong";
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function EmailSelectionProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [messages, setMessages] = useState<GmailMessageSummary[]>([]);
  const [selected, setSelected] = useState<GmailMessageSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Fetching and applying are separate so the mount effect never sets state
  // synchronously, and the Refresh button can show its own busy state.
  const applyMessages = useCallback((result: GmailMessageList) => {
    setMessages(result.messages);
    // Deliberately no default selection. Step 1 of the workflow *sends* a real
    // reply, so silently pre-selecting the newest inbox message would let a
    // run answer an unrelated newsletter or notification. With nothing chosen
    // the workflow uses the operator's configured Gmail query instead, and
    // says so plainly if that message has not arrived.
    setSelected((current) => {
      if (!current) return null;
      return (
        result.messages.find((item) => item.message_id === current.message_id) ??
        null
      );
    });
  }, []);

  const load = useCallback(() => listGmailMessages(10), []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      applyMessages(await load());
    } catch (failure) {
      setMessages([]);
      setSelected(null);
      setError(describe(failure));
    } finally {
      setLoading(false);
    }
  }, [applyMessages, load]);

  useEffect(() => {
    let cancelled = false;
    load().then(
      (result) => {
        if (!cancelled) applyMessages(result);
      },
      (failure: unknown) => {
        if (cancelled) return;
        setMessages([]);
        setSelected(null);
        setError(describe(failure));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [applyMessages, load]);

  const value = useMemo<EmailSelectionValue>(
    () => ({
      messages,
      selected,
      loading,
      error,
      connected: !error || !/authentication required/i.test(error),
      select: setSelected,
      refresh,
    }),
    [messages, selected, loading, error, refresh],
  );

  return (
    <EmailSelectionContext.Provider value={value}>
      {children}
    </EmailSelectionContext.Provider>
  );
}

export function useEmailSelection(): EmailSelectionValue {
  const context = useContext(EmailSelectionContext);
  if (!context) {
    throw new Error("useEmailSelection must be used inside EmailSelectionProvider");
  }
  return context;
}

export function EmailPicker() {
  const {
    messages,
    selected,
    loading,
    error,
    select,
    refresh,
  } = useEmailSelection();
  // Collapsed by default: picking a specific message is optional (the
  // workflow reads the newest matching message without it), and an expanded
  // inbox list pushed the discovered workflow — the thing the demo is about —
  // below the fold.
  const [open, setOpen] = useState(false);

  return (
    <section className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.16em] text-zinc-500 transition-colors hover:text-zinc-300"
        >
          <span>{open ? "▾" : "▸"}</span>
          Select Email
          <span className="text-[11px] font-normal normal-case tracking-normal text-zinc-600">
            {selected
              ? `· 1 of ${messages.length} selected`
              : `· optional — ${messages.length} in inbox`}
          </span>
        </button>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="rounded-lg border border-white/[0.1] px-2.5 py-1 text-[11px] text-zinc-300 transition-colors hover:border-white/[0.25] disabled:opacity-60"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {open && (
        <>
          <p className="mb-3 text-[11px] leading-relaxed text-zinc-600">
            Messages in your connected Gmail inbox. The one you select is read
            by step&nbsp;1 of the workflow and sent to the local model in
            step&nbsp;2. Anything the connected account sent itself is marked,
            so an incoming email is never confused with a reply WorkFlowOS
            produced.
          </p>

      {error && (
        <p className="rounded-lg border border-amber-400/25 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-300">
          {error}
        </p>
      )}

      {!error && messages.length === 0 && !loading && (
        <p className="rounded-lg border border-white/[0.07] bg-black/20 px-3 py-2 text-[11px] text-zinc-500">
          No messages returned by Gmail.
        </p>
      )}

      {messages.length > 0 && (
        <ul className="max-h-80 space-y-1.5 overflow-y-auto pr-1">
          {messages.map((message) => {
            const active = selected?.message_id === message.message_id;
            return (
              <li key={message.message_id}>
                <button
                  type="button"
                  onClick={() => select(message)}
                  aria-pressed={active}
                  className={`w-full rounded-xl border px-3 py-2.5 text-left transition-colors ${
                    active
                      ? "border-brand-400/50 bg-brand-500/10"
                      : "border-white/[0.07] bg-white/[0.02] hover:border-white/[0.2]"
                  }`}
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="truncate text-xs text-zinc-200">
                      {message.is_self_sent
                        ? "Sent by this account"
                        : `Incoming email from: ${message.sender || "unknown sender"}`}
                    </span>
                    <span className="shrink-0 text-[10px] text-zinc-600">
                      {formatDate(message.date)}
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-zinc-400">
                    {message.subject || "(no subject)"}
                  </p>
                  {message.snippet && (
                    <p className="mt-0.5 truncate text-[11px] text-zinc-600">
                      {message.snippet}
                    </p>
                  )}
                  <p className="mt-1 font-mono text-[10px] text-zinc-700">
                    {message.message_id}
                  </p>
                </button>
              </li>
            );
          })}
          </ul>
        )}
        </>
      )}

      <div className="mt-4 rounded-xl border border-white/[0.07] bg-black/20 px-3 py-2.5">
        <p className="text-[10px] uppercase tracking-[0.16em] text-zinc-600">
          Selected for this run
        </p>
        {selected ? (
          <div className="mt-1 space-y-0.5">
            <p className="truncate text-xs text-zinc-200">
              {selected.subject || "(no subject)"}
            </p>
            <p
              className={`truncate text-[11px] ${
                selected.is_self_sent ? "text-amber-300/80" : "text-zinc-500"
              }`}
            >
              {selected.is_self_sent
                ? `Sent by this account (${selected.sender})`
                : `Incoming email from: ${selected.sender}`}
            </p>
            <p className="font-mono text-[10px] text-zinc-600">
              {selected.message_id}
            </p>
          </div>
        ) : (
          <p className="mt-1 text-[11px] text-zinc-500">
            Nothing selected. The newest message will be used.
          </p>
        )}
      </div>
    </section>
  );
}
