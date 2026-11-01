import { expect, test } from "@rstest/core";

import { isVideoFile } from "@/core/utils/files";

test("isVideoFile matches browser-playable video extensions", () => {
  for (const file of ["a.mp4", "b.mov", "c.m4v", "d.webm", "e.MP4"]) {
    expect(isVideoFile(file)).toBe(true);
  }
});

test("isVideoFile rejects non-video files", () => {
  for (const file of [
    "a.jpg",
    "b.png",
    "c.txt",
    "d.mp3",
    "e.wav",
    "video",
    "f.mp4.txt",
  ]) {
    expect(isVideoFile(file)).toBe(false);
  }
});
