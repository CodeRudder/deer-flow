import { describe, expect, test } from "@rstest/core";

import { isMobileUserAgent, MOBILE_BREAKPOINT } from "@/lib/device";

/** Real-world UA strings, trimmed from captures of the actual clients. */
const IPHONE_SAFARI =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";
const ANDROID_CHROME =
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36";
const ANDROID_WEBVIEW =
  "Mozilla/5.0 (Linux; Android 13; SM-S901B Build/TP1A.220624.014; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/119.0.0.0 Mobile Safari/537.36";
const IPAD_SAFARI =
  "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";
const IPADOS_DESKTOP_MODE =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15";
const ANDROID_TABLET =
  "Mozilla/5.0 (Linux; Android 13; SM-X700) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";
const CHROME_DESKTOP =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";
const SAFARI_DESKTOP =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15";
const FIREFOX_DESKTOP =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0";
const GOOGLEBOT =
  "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)";
const BINGBOT =
  "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)";

describe("isMobileUserAgent", () => {
  describe("phones are mobile", () => {
    test("iPhone Safari", () => {
      expect(isMobileUserAgent(IPHONE_SAFARI)).toBe(true);
    });

    test("Android Chrome", () => {
      expect(isMobileUserAgent(ANDROID_CHROME)).toBe(true);
    });

    test("Android WebView", () => {
      expect(isMobileUserAgent(ANDROID_WEBVIEW)).toBe(true);
    });
  });

  describe("tablets fall through to the desktop pages", () => {
    test("iPad Safari carries the Mobile/ token but stays desktop", () => {
      expect(isMobileUserAgent(IPAD_SAFARI)).toBe(false);
    });

    test("iPadOS desktop-mode Safari is desktop", () => {
      expect(isMobileUserAgent(IPADOS_DESKTOP_MODE)).toBe(false);
    });

    test("Android tablet has no Mobile token and stays desktop", () => {
      expect(isMobileUserAgent(ANDROID_TABLET)).toBe(false);
    });
  });

  describe("desktop browsers stay desktop", () => {
    test("Chrome", () => {
      expect(isMobileUserAgent(CHROME_DESKTOP)).toBe(false);
    });

    test("Safari", () => {
      expect(isMobileUserAgent(SAFARI_DESKTOP)).toBe(false);
    });

    test("Firefox", () => {
      expect(isMobileUserAgent(FIREFOX_DESKTOP)).toBe(false);
    });
  });

  describe("unknown clients fail safe to desktop", () => {
    test("undefined", () => {
      expect(isMobileUserAgent(undefined)).toBe(false);
    });

    test("null", () => {
      expect(isMobileUserAgent(null)).toBe(false);
    });

    test("empty string", () => {
      expect(isMobileUserAgent("")).toBe(false);
    });

    test("whitespace only", () => {
      expect(isMobileUserAgent("   ")).toBe(false);
    });

    test("garbage", () => {
      expect(isMobileUserAgent("not-a-real-user-agent")).toBe(false);
    });
  });

  describe("crawlers stay desktop", () => {
    test("Googlebot", () => {
      expect(isMobileUserAgent(GOOGLEBOT)).toBe(false);
    });

    test("bingbot", () => {
      expect(isMobileUserAgent(BINGBOT)).toBe(false);
    });

    test("Googlebot Smartphone is still a crawler", () => {
      expect(
        isMobileUserAgent(
          "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        ),
      ).toBe(false);
    });
  });

  test("exposes the 768px breakpoint shared with use-mobile", () => {
    expect(MOBILE_BREAKPOINT).toBe(768);
  });
});
