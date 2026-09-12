import { devices, expect, test, type Page, type Route } from "@playwright/test";

import { MOCK_RUN_ID, MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

/**
 * T12 / F4-9 + F5-6 — the attachment chain *after* a file has been chosen.
 *
 * `mobile-chat.spec.ts` proves the `＋` panel lists 相册 / 拍照 / 文件; what no
 * test covered is what happens next. This file closes that gap:
 *
 * 1. each entry is wired to its own `<input type="file">` (相册 vs 拍照 must not
 *    be the same input — the camera one has to carry `capture`), and a chosen
 *    file joins the visible pending list;
 * 2. an attachment can be removed again before sending;
 * 3. sending uploads it (`POST /api/threads/{id}/uploads`) and the *uploaded*
 *    path — not the local filename — reaches the run payload;
 * 4. a failed upload reports the error and keeps both the attachment and the
 *    typed text.
 *
 * The OS picker cannot be driven from a browser test, so the pickers are
 * exercised two ways: `page.setInputFiles()` on the composer's hidden inputs for
 * the file-carrying flows, and one `filechooser` round-trip per entry to prove
 * which input each entry actually opens. `capture`/`accept` are read off the
 * real elements rather than trusted.
 *
 * ── Known defect (T5's 相册 / 拍照 entries) ──────────────────────────────────
 * 相册 and 拍照 drop the chosen file on the floor; only 文件 works. The two
 * broken entries are pinned by `test.fail()` tests below — they will turn red
 * the moment the bug is fixed, which is the point. The three entries' wiring is
 * *not* at fault: they do open three distinct inputs (asserted here).
 *
 * Note on the failure test (4): the upload path is the one place
 * `handleSubmit`'s promise/void asymmetry genuinely applied, and T11 made
 * `handleSubmit` return the promise on both paths. So an upload rejection
 * reaches `prompt-input.tsx`'s "Don't clear on error" branch and nothing has to
 * be handed back later — unlike a failed *run*, whose promise the SDK resolves
 * before the request is even sent.
 */

test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const CHAT_PATH = "/workspace/chats/new";

/** 1x1 PNG — small, and a real image so the entries' `image/*` filter accepts it. */
const PNG_BYTES = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==",
  "base64",
);

function png(name: string) {
  return { name, mimeType: "image/png", buffer: PNG_BYTES };
}

const GALLERY_INPUT = '[data-testid="mobile-composer-gallery-input"]';
const CAMERA_INPUT = '[data-testid="mobile-composer-camera-input"]';
/** The prompt primitive's own hidden input — the one 文件 calls `openFileDialog` on. */
const PRIMITIVE_INPUT = 'input[type="file"][aria-label="Upload files"]';

const UPLOADED_NAME = "trip.png";
const UPLOADED_VIRTUAL_PATH = `/mnt/user-data/uploads/${UPLOADED_NAME}`;
const UPLOADED_SIZE = 12;

/**
 * A single thinking-capable model, so the composer pins `context.mode` on mount
 * and takes the direct submit branch (the deferred branch is not the path a real
 * send takes once the model list has loaded).
 */
function mockModels(page: Page) {
  return page.route("**/api/models", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        models: [
          {
            id: "mock-model",
            name: "mock-model",
            model: "mock-model",
            display_name: "Mock Model",
            supports_thinking: true,
          },
        ],
        vision_models: [],
        token_usage: { enabled: false },
      }),
    }),
  );
}

/** The successful `POST /api/threads/{id}/uploads` response shape. */
function uploadOkBody(filename: string) {
  return JSON.stringify({
    success: true,
    message: "Uploaded",
    files: [
      {
        filename,
        size: UPLOADED_SIZE,
        path: filename,
        virtual_path: UPLOADED_VIRTUAL_PATH,
        artifact_url: `/api/threads/test/uploads/${filename}`,
      },
    ],
  });
}

function sse() {
  return (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      // The SDK reads the created run's thread id from `Content-Location`.
      headers: {
        "Content-Location": `/threads/${MOCK_THREAD_ID}/runs/${MOCK_RUN_ID}`,
      },
      body: [
        {
          event: "metadata",
          data: { run_id: MOCK_RUN_ID, thread_id: MOCK_THREAD_ID },
        },
        {
          event: "values",
          data: {
            messages: [
              {
                type: "human",
                id: "msg-human-1",
                content: [{ type: "text", text: "Hello" }],
              },
              { type: "ai", id: "msg-ai-1", content: "Hello from DeerFlow!" },
            ],
          },
        },
        { event: "end", data: {} },
      ]
        .map(
          (event) =>
            `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`,
        )
        .join(""),
    });
}

type RunMessage = {
  content?: unknown;
  additional_kwargs?: { files?: unknown[] };
};

/** Records the JSON body of every run-stream request. */
function recordStreamBodies(page: Page) {
  const bodies: Record<string, unknown>[] = [];
  void page.route("**/runs/stream", (route) => {
    bodies.push(route.request().postDataJSON() as Record<string, unknown>);
    return sse()(route);
  });
  return bodies;
}

/** The single human message of the run payload's most recent request. */
function lastRunMessage(body: Record<string, unknown> | undefined) {
  const input = (body as { input?: { messages?: RunMessage[] } } | undefined)
    ?.input;
  return input?.messages?.at(-1);
}

/** Flatten a LangGraph message content (string or text blocks) to plain text. */
function contentText(content: unknown) {
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .map((block) =>
        typeof block === "object" &&
        block !== null &&
        "text" in block &&
        typeof block.text === "string"
          ? block.text
          : "",
      )
      .join("");
  }
  return "";
}

function textarea(page: Page) {
  return page.getByTestId("mobile-composer").locator("textarea");
}

function chips(page: Page) {
  return page.getByTestId("mobile-composer-attachment");
}

function plus(page: Page) {
  return page.getByTestId("mobile-composer-plus");
}

async function openComposer(page: Page) {
  await page.goto(CHAT_PATH);
  await expect(page.getByTestId("mobile-composer")).toBeVisible({
    timeout: 15_000,
  });
}

/** Dismiss the `＋` sheet: the attachment chips sit in the composer behind it. */
async function closeSheet(page: Page) {
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("mobile-composer-sheet")).toHaveCount(0);
}

/**
 * Click a `＋` entry and return the file chooser it opened, together with the
 * identifier of the input the chooser is attached to. This is what proves each
 * entry opens *its own* input rather than all three sharing one.
 */
async function openEntry(page: Page, entry: string) {
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser", { timeout: 10_000 }),
    page.getByTestId(`mobile-composer-sheet-${entry}`).click(),
  ]);
  const element = chooser.element();
  const identify =
    (await element.getAttribute("data-testid")) ??
    (await element.getAttribute("aria-label"));
  return { chooser, identify };
}

// ---------------------------------------------------------------------------
// Picker wiring and attributes
// ---------------------------------------------------------------------------

test.describe("Mobile attachments — picker wiring", () => {
  test("each ＋ entry opens its own file input, with the camera one capturing", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    await openComposer(page);

    await plus(page).click();
    await expect(page.getByTestId("mobile-composer-sheet")).toBeVisible();

    // 相册 → the composer's gallery input; 拍照 → its camera input; 文件 → the
    // prompt primitive's own input.
    const gallery = await openEntry(page, "gallery");
    expect(gallery.identify).toBe("mobile-composer-gallery-input");
    expect(gallery.chooser.isMultiple()).toBe(true);

    const camera = await openEntry(page, "camera");
    expect(camera.identify).toBe("mobile-composer-camera-input");

    const file = await openEntry(page, "file");
    expect(file.identify).toBe("Upload files");
    expect(file.chooser.isMultiple()).toBe(true);

    // The attributes the OS picker reads. `capture="environment"` is the whole
    // difference between 相册 and 拍照 — the OS picker cannot be driven here, so
    // the attribute is the assertion.
    const attrs = await page.evaluate(
      ([gallerySelector, cameraSelector, primitiveSelector]) => {
        const read = (selector: string) => {
          const input = document.querySelector<HTMLInputElement>(selector);
          return input
            ? {
                type: input.type,
                accept: input.getAttribute("accept"),
                capture: input.getAttribute("capture"),
                multiple: input.multiple,
              }
            : null;
        };
        return {
          gallery: read(gallerySelector!),
          camera: read(cameraSelector!),
          file: read(primitiveSelector!),
        };
      },
      [GALLERY_INPUT, CAMERA_INPUT, PRIMITIVE_INPUT],
    );

    expect(attrs.gallery).toEqual({
      type: "file",
      accept: "image/*",
      capture: null,
      multiple: true,
    });
    // 拍照 is single-shot on purpose: one press of the shutter, one file.
    expect(attrs.camera).toEqual({
      type: "file",
      accept: "image/*",
      capture: "environment",
      multiple: false,
    });
    // 文件 is a general picker: no type filter, no capture hint.
    expect(attrs.file).toEqual({
      type: "file",
      accept: null,
      capture: null,
      multiple: true,
    });
  });
});

// ---------------------------------------------------------------------------
// 相册 / 拍照 — currently broken, pinned as expected failures
// ---------------------------------------------------------------------------

test.describe("Mobile attachments — 相册 / 拍照 (known defect)", () => {
  /*
   * Mechanism (measured, not inferred).
   *
   * `composer.tsx`'s `handlePicked` reads the FileList *object* and then resets
   * the input to allow re-picking the same file:
   *
   *     const { files } = event.target;   // FileList object, not a copy
   *     event.target.value = "";          // empties that same object
   *     if (!files || files.length === 0) return;   // → always 0, always bails
   *
   * Per the HTML spec the `files` getter returns *the same* FileList object
   * "until the list of selected files changes", and setting `value = ""` is
   * exactly such a change. Probed in the same Chromium the suite runs:
   *
   *     {"before":1, "names":["probe.png"], "after":0, "sameAcrossAccess":true}
   *
   * So both entries that route through `handlePicked` (相册 and 拍照) silently
   * drop every file, while 文件 — which goes through `prompt-input.tsx`'s
   * `handleChange` and snapshots the list (`Array.from`) *before* resetting —
   * works. The fix is to snapshot first (`Array.from(event.target.files)`) or to
   * reset the value after `attachments.add(...)`.
   *
   * These two tests therefore assert the *intended* behaviour and are marked
   * `test.fail()`: they are green today only because Playwright expects them to
   * fail, and they will go red (loudly) as soon as the defect is fixed.
   */

  test.fail(
    "a photo chosen from 相册 joins the pending list",
    async ({ page }) => {
      mockLangGraphAPI(page, { threads: [] });
      await mockModels(page);
      await openComposer(page);

      await page.locator(GALLERY_INPUT).setInputFiles(png("trip.png"));

      await expect(chips(page)).toHaveCount(1, { timeout: 5_000 });
      await expect(chips(page)).toContainText("trip.png");
    },
  );

  test.fail(
    "a photo taken with 拍照 joins the pending list",
    async ({ page }) => {
      mockLangGraphAPI(page, { threads: [] });
      await mockModels(page);
      await openComposer(page);

      await page.locator(CAMERA_INPUT).setInputFiles(png("shot.png"));

      await expect(chips(page)).toHaveCount(1, { timeout: 5_000 });
      await expect(chips(page)).toContainText("shot.png");
    },
  );
});

// ---------------------------------------------------------------------------
// 文件 — the entry that does work
// ---------------------------------------------------------------------------

test.describe("Mobile attachments — 文件", () => {
  test("chosen files join the pending list and can be removed again", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    await openComposer(page);

    await plus(page).click();
    await expect(page.getByTestId("mobile-composer-sheet")).toBeVisible();

    const file = await openEntry(page, "file");
    await file.chooser.setFiles([png("first.png"), png("second.png")]);
    await closeSheet(page);

    // Visible to the user, by name, before anything is sent.
    await expect(chips(page)).toHaveCount(2);
    expect(
      (await chips(page).allTextContents()).map((text) => text.trim()).sort(),
    ).toEqual(["first.png", "second.png"]);

    // And removable again — the chip's remove button is a real (44px) control,
    // not the primitive's hover-only one. Only the named chip goes.
    await chips(page).filter({ hasText: "first.png" }).locator("button").click();
    await expect(chips(page)).toHaveCount(1);
    expect(
      (await chips(page).allTextContents()).map((text) => text.trim()),
    ).toEqual(["second.png"]);
  });

  test("sending uploads the file and hands the uploaded path to the run", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);

    const uploads: { method: string; path: string; body: string }[] = [];
    await page.route("**/api/threads/*/uploads", (route) => {
      const request = route.request();
      uploads.push({
        method: request.method(),
        path: new URL(request.url()).pathname,
        body: request.postData() ?? "",
      });
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: uploadOkBody(UPLOADED_NAME),
      });
    });
    const bodies = recordStreamBodies(page);

    await openComposer(page);
    await page.locator(PRIMITIVE_INPUT).setInputFiles(png(UPLOADED_NAME));
    await textarea(page).fill("Where was this taken?");

    await expect(chips(page)).toHaveCount(1);
    await expect(chips(page)).toContainText(UPLOADED_NAME);

    await page.getByTestId("mobile-composer-send").click();

    // 1. The file really left the browser, on the upload endpoint.
    await expect.poll(() => uploads.length, { timeout: 10_000 }).toBe(1);
    expect(uploads[0]?.method).toBe("POST");
    expect(uploads[0]?.path).toMatch(/^\/api\/threads\/.+\/uploads$/);
    expect(uploads[0]?.body).toContain(`filename="${UPLOADED_NAME}"`);

    // 2. The run carries the *server* path from the upload response, not the
    //    local filename — pending-list state alone would not prove this.
    await expect.poll(() => bodies.length, { timeout: 10_000 }).toBe(1);
    const message = lastRunMessage(bodies[0]);
    expect(message?.additional_kwargs?.files).toEqual([
      {
        filename: UPLOADED_NAME,
        size: UPLOADED_SIZE,
        path: UPLOADED_VIRTUAL_PATH,
        status: "uploaded",
      },
    ]);
    expect(contentText(message?.content)).toBe("Where was this taken?");

    // 3. The pending list is emptied by the successful send.
    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 10_000,
    });
    await expect(chips(page)).toHaveCount(0);
  });

  test("a failed upload reports the error and keeps the attachment and the text", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);

    await page.route("**/api/threads/*/uploads", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "attachment rejected by gateway" }),
      }),
    );
    const bodies = recordStreamBodies(page);

    await openComposer(page);
    await page.locator(PRIMITIVE_INPUT).setInputFiles(png(UPLOADED_NAME));
    await textarea(page).fill("Where was this taken?");

    await page.getByTestId("mobile-composer-send").click();

    // The gateway's own message is what the user sees.
    const toast = page.locator("[data-sonner-toast]").first();
    await expect(toast).toBeVisible({ timeout: 10_000 });
    await expect(toast).toContainText("attachment rejected by gateway");

    // Neither half of the message is dropped: the attachment is still pending
    // and the text is still in the composer, so the send is retryable.
    await expect(chips(page)).toHaveCount(1);
    await expect(chips(page)).toContainText(UPLOADED_NAME);
    await expect(textarea(page)).toHaveValue("Where was this taken?");

    // The send aborted at the upload — nothing reached the run endpoint.
    expect(bodies).toHaveLength(0);
  });
});
