import { describe, expect, it } from "vitest";

import { enqueueSerial } from "./serialQueue";

describe("enqueueSerial", () => {
  it("keeps later persistence payloads behind earlier saves", async () => {
    const tail = { current: Promise.resolve() };
    const events: string[] = [];
    let releaseFirst = () => {};
    const firstCanFinish = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });

    const first = enqueueSerial(tail, async () => {
      events.push("done:start");
      await firstCanFinish;
      events.push("done:end");
      return "done";
    });
    const second = enqueueSerial(tail, async () => {
      events.push("suggestions:start");
      return "suggestions";
    });

    await Promise.resolve();
    expect(events).toEqual(["done:start"]);
    releaseFirst();

    await expect(Promise.all([first, second])).resolves.toEqual([
      "done",
      "suggestions",
    ]);
    expect(events).toEqual(["done:start", "done:end", "suggestions:start"]);
  });
});
