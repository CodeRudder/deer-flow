import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";

import {
  formatThinkingDuration,
  useGetThinkingMessage,
} from "@/components/workspace/messages/thinking-message";
import { I18nProvider } from "@/core/i18n/context";
import type { Locale } from "@/core/i18n/locale";
import { translations } from "@/core/i18n/translations";

const zh = translations["zh-CN"].toolCalls;
const en = translations["en-US"].toolCalls;

/**
 * The live branch must tick, so the clock has to be ours: `Date` is faked
 * explicitly because the elapsed counter is derived from `Date.now()`.
 */
type FakeTimersConfig = NonNullable<Parameters<typeof rs.useFakeTimers>[0]>;

const FAKE_TIMERS: FakeTimersConfig = {
  toFake: [
    "setInterval",
    "clearInterval",
    "setTimeout",
    "clearTimeout",
    "Date",
  ],
};

type ProbeProps = {
  isStreaming: boolean;
  duration?: number;
  startTime?: number | null;
};

let lastCallback: ReturnType<typeof useGetThinkingMessage> | undefined;

function TriggerProbe({
  isStreaming,
  duration,
  startTime,
}: ProbeProps): ReactNode {
  const getThinkingMessage = useGetThinkingMessage();
  lastCallback = getThinkingMessage;
  return <>{getThinkingMessage(isStreaming, duration, startTime)}</>;
}

/**
 * Renders through the real `I18nProvider`. The cookie is written first because
 * `useI18n` re-reads it on mount, so this proves the wording follows the app
 * locale rather than a string threaded in by the test.
 */
function renderProbe(locale: Locale, props: ProbeProps) {
  document.cookie = `locale=${locale}; path=/`;
  return render(
    <I18nProvider initialLocale={locale}>
      <TriggerProbe {...props} />
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  rs.useRealTimers();
  document.cookie = "locale=; max-age=0; path=/";
  lastCallback = undefined;
});

describe("formatThinkingDuration", () => {
  it("keeps the registry's second/minute switch in the locale's units", () => {
    expect(formatThinkingDuration(zh, 12)).toBe("12 秒");
    expect(formatThinkingDuration(en, 12)).toBe("12s");
    expect(formatThinkingDuration(zh, 59)).toBe("59 秒");
    expect(formatThinkingDuration(zh, 60)).toBe("1 分 0 秒");
    expect(formatThinkingDuration(en, 60)).toBe("1m 0s");
    expect(formatThinkingDuration(zh, 65)).toBe("1 分 5 秒");
    expect(formatThinkingDuration(en, 65)).toBe("1m 5s");
  });

  it("clamps and floors like the registry formatter", () => {
    expect(formatThinkingDuration(zh, -3)).toBe("0 秒");
    expect(formatThinkingDuration(zh, 12.8)).toBe("12 秒");
  });
});

describe("thinking message, settled", () => {
  it("renders the localized 'thought for N' wording", () => {
    const zhText = renderProbe("zh-CN", {
      isStreaming: false,
      duration: 12,
    }).container.textContent;
    cleanup();
    const enText = renderProbe("en-US", {
      isStreaming: false,
      duration: 12,
    }).container.textContent;

    expect(zhText).toContain("思考了 12 秒");
    expect(zhText).not.toContain("Thought for");
    expect(enText).toContain("Thought for 12s");
    expect(enText).not.toContain("思考了");
  });

  it("renders the localized fallback when there is no duration", () => {
    const zhText = renderProbe("zh-CN", { isStreaming: false }).container
      .textContent;
    cleanup();
    const enText = renderProbe("en-US", { isStreaming: false }).container
      .textContent;

    expect(zhText).toContain("思考了几秒");
    expect(enText).toContain("Thought for a few seconds");
  });

  it("treats a non-finite duration like the registry does", () => {
    const zhText = renderProbe("zh-CN", {
      isStreaming: false,
      duration: Number.NaN,
    }).container.textContent;

    expect(zhText).toContain("思考了几秒");
    expect(zhText).not.toContain("NaN");
  });
});

describe("thinking message, streaming", () => {
  it("ticks once a second with the localized label and counter", () => {
    rs.useFakeTimers(FAKE_TIMERS);
    const startTime = Date.now() - 12_000;

    const { container, unmount } = renderProbe("zh-CN", {
      isStreaming: true,
      startTime,
    });

    expect(container.textContent).toContain("思考中…");
    expect(container.textContent).toContain("(12 秒)");

    act(() => {
      rs.advanceTimersByTime(2_000);
    });

    // The tick is the point: freezing at the first frame would be a regression.
    expect(container.textContent).toContain("(14 秒)");
    expect(container.textContent).not.toContain("(12 秒)");

    act(() => {
      rs.advanceTimersByTime(60_000);
    });
    expect(container.textContent).toContain("(1 分 14 秒)");

    unmount();
  });

  it("ticks with English units under en-US", () => {
    rs.useFakeTimers(FAKE_TIMERS);
    const startTime = Date.now() - 3_000;

    const { container } = renderProbe("en-US", {
      isStreaming: true,
      startTime,
    });

    expect(container.textContent).toContain("Thinking…");
    expect(container.textContent).toContain("(3s)");

    act(() => {
      rs.advanceTimersByTime(1_000);
    });
    expect(container.textContent).toContain("(4s)");
  });

  it("falls back to a bare localized label when streaming has no start time", () => {
    const zhText = renderProbe("zh-CN", {
      isStreaming: true,
      startTime: null,
    }).container.textContent;
    cleanup();
    const enText = renderProbe("en-US", {
      isStreaming: true,
      startTime: null,
    }).container.textContent;

    expect(zhText).toContain("思考中…");
    expect(zhText).not.toContain("思考了");
    expect(enText).toContain("Thinking…");
    expect(enText).not.toContain("Thought for");
  });
});

describe("thinking message callback identity", () => {
  it("stays stable across re-renders so the memoized ReasoningTrigger holds", () => {
    const { rerender } = renderProbe("zh-CN", { isStreaming: false });
    const first = lastCallback;

    rerender(
      <I18nProvider initialLocale="zh-CN">
        <TriggerProbe isStreaming={false} />
      </I18nProvider>,
    );

    expect(first).toBeDefined();
    expect(lastCallback).toBe(first);
  });
});
