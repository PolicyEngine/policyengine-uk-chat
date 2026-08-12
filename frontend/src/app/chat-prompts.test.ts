import { describe, expect, it } from "vitest";

import { STARTER_PROMPTS } from "./chat-prompts";

describe("STARTER_PROMPTS", () => {
  it("uses complete questions for every empty-state prompt", () => {
    expect(STARTER_PROMPTS).toEqual([
      "What's the personal allowance?",
      "How much tax would I pay on £50,000 of income?",
      "How much Child Benefit would I receive for three children?",
      "How does marriage allowance work?",
    ]);
  });
});
