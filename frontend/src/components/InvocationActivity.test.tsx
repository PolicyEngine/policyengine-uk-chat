import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import InvocationActivity, {
  mergeInvocationActivity,
  type InvocationActivityItem,
} from "./InvocationActivity";


const invocation = (
  invocation_id: string,
  sequence: number,
  overrides: Partial<InvocationActivityItem> = {},
): InvocationActivityItem => ({
  turn_id: "turn-1",
  invocation_id,
  parent_invocation_id: null,
  sequence,
  kind: "capability",
  identifier: invocation_id,
  version: "1",
  visibility: "public",
  started_at: `2026-01-01T00:00:0${sequence}Z`,
  completed_at: null,
  duration_ms: null,
  status: "running",
  summary: "sanitized summary",
  ...overrides,
});


describe("InvocationActivity", () => {
  it("merges start and finish updates without duplicating a call", () => {
    const started = invocation("policy_information", 1);
    const finished = { ...started, status: "completed" as const, duration_ms: 14 };

    expect(mergeInvocationActivity([started], finished)).toEqual([finished]);
  });

  it("orders calls, shows nesting, repeated attempts, failures, and private visibility", () => {
    const parent = invocation("society_analysis", 1, {
      debug_input: {
        reform_instruction: "Raise the allowance",
        candidate: {
          people: [{ age: 42 }],
        },
      },
      debug_output: { status: "completed", output_count: 3 },
    });
    const first = invocation("validate_reform-1", 2, {
      identifier: "validate_reform",
      kind: "tool",
      visibility: "private",
      parent_invocation_id: parent.invocation_id,
      status: "failed",
      duration_ms: 3,
    });
    const second = invocation("validate_reform-2", 3, {
      identifier: "validate_reform",
      kind: "tool",
      visibility: "private",
      parent_invocation_id: parent.invocation_id,
      status: "completed",
      duration_ms: 2,
    });

    render(<InvocationActivity invocations={[second, parent, first]} debug />);

    const activity = screen.getByRole("region", { name: "Invocation activity" });
    const rows = within(activity).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("society_analysis");
    expect(rows[1]).toHaveTextContent("validate_reform");
    expect(rows[1]).toHaveTextContent("failed");
    expect(rows[2]).toHaveTextContent("attempt 2");
    expect(within(activity).getAllByText("private")).toHaveLength(2);

    const parentDetails = within(activity).getByRole("button", {
      name: "Toggle society_analysis details",
    });
    expect(parentDetails).toHaveAttribute("aria-expanded", "false");
    expect(within(activity).queryByText("Input")).not.toBeInTheDocument();
    fireEvent.click(parentDetails);
    expect(parentDetails).toHaveAttribute("aria-expanded", "true");
    const inputSummary = within(activity).getByText("Input");
    const outputSummary = within(activity).getByText("Output");
    expect(inputSummary.closest("details")).not.toHaveAttribute("open");
    expect(outputSummary.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(inputSummary);
    expect(inputSummary.closest("details")).toHaveAttribute("open");
    expect(within(activity).getByText(/Raise the allowance/)).toBeInTheDocument();
    const candidate = within(activity).getByText("candidate");
    const people = within(activity).getByText("people");
    const firstPerson = within(activity).getByText("[0]");
    expect(candidate.closest("details")).not.toHaveAttribute("open");
    expect(people.closest("details")).not.toHaveAttribute("open");
    expect(firstPerson.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(candidate);
    expect(candidate.closest("details")).toHaveAttribute("open");
    fireEvent.click(people);
    expect(people.closest("details")).toHaveAttribute("open");
    fireEvent.click(firstPerson);
    expect(firstPerson.closest("details")).toHaveAttribute("open");
    expect(within(activity).getByText("age:")).toBeInTheDocument();
    expect(within(activity).getByText("42")).toBeInTheDocument();
    fireEvent.click(outputSummary);
    expect(outputSummary.closest("details")).toHaveAttribute("open");
    expect(within(activity).getByText("output_count:")).toBeInTheDocument();

    fireEvent.click(
      within(activity).getByRole("button", { name: /Activity ·/ }),
    );
    expect(within(activity).queryByRole("list")).not.toBeInTheDocument();
  });

  it("does not render private records in the normal projection", () => {
    render(
      <InvocationActivity
        invocations={[
          invocation("public-call", 1),
          invocation("private-call", 2, { visibility: "private" }),
        ]}
        debug={false}
      />,
    );

    expect(screen.getByText("public-call")).toBeInTheDocument();
    expect(screen.queryByText("private-call")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Toggle public-call details" }),
    ).not.toBeInTheDocument();
  });

  it("lets debug users inspect structured fact-reduction decisions", () => {
    render(
      <InvocationActivity
        debug
        invocations={[
          invocation("reduce-context", 1, {
            identifier: "reduce_context_patch",
            kind: "tool",
            visibility: "private",
            status: "completed",
            debug_input: {
              patch: {
                expected_revision: 4,
                operations: [{ definition_key: "person.age" }],
              },
            },
            debug_output: {
              context: { revision: 5 },
              decisions: [
                {
                  status: "superseded",
                  definition_key: "person.age",
                  subject_entity_id: "person:self",
                  superseded_fact_id: "fact-before",
                },
              ],
            },
          }),
        ]}
      />,
    );

    const activity = screen.getByRole("region", { name: "Invocation activity" });
    fireEvent.click(
      within(activity).getByRole("button", {
        name: "Toggle reduce_context_patch details",
      }),
    );
    fireEvent.click(within(activity).getByText("Output"));
    const outputTree = within(activity).getByRole("tree", { name: "Output JSON" });
    fireEvent.click(within(outputTree).getByText("decisions"));
    fireEvent.click(within(outputTree).getByText("[0]"));

    expect(within(outputTree).getByText('"superseded"')).toBeInTheDocument();
    expect(within(outputTree).getByText(/person\.age/)).toBeInTheDocument();
    expect(within(outputTree).getByText(/person:self/)).toBeInTheDocument();
  });
});
