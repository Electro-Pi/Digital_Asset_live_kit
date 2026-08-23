import { describe, expect, it } from "vitest";
import {
  MAX_AGENT_MINUTES,
  bookingStatusSchema,
  planLimitsSchema,
  userLimitsSchema,
} from "@/lib/validation";

describe("admin limit validation", () => {
  it("accepts zero plan limits", () => {
    expect(
      planLimitsSchema.parse({ agentMinutes: 0, ttsRequests: 0, sttRequests: 0, llmRequests: 0 }),
    ).toEqual({ agentMinutes: 0, ttsRequests: 0, sttRequests: 0, llmRequests: 0 });
  });

  it("accepts nullable per-user inheritance", () => {
    expect(
      userLimitsSchema.parse({
        agentMinutes: null,
        ttsRequests: 100,
        sttRequests: null,
        llmRequests: 0,
      }),
    ).toEqual({ agentMinutes: null, ttsRequests: 100, sttRequests: null, llmRequests: 0 });
  });

  it.each([
    { agentMinutes: -1, ttsRequests: 0, sttRequests: 0, llmRequests: 0 },
    { agentMinutes: 1.5, ttsRequests: 0, sttRequests: 0, llmRequests: 0 },
    { agentMinutes: MAX_AGENT_MINUTES + 1, ttsRequests: 0, sttRequests: 0, llmRequests: 0 },
  ])("rejects values that cannot safely reach PostgreSQL: %o", (payload) => {
    expect(planLimitsSchema.safeParse(payload).success).toBe(false);
  });

  it("allows only the five booking states", () => {
    expect(bookingStatusSchema.safeParse({ status: "scheduled" }).success).toBe(true);
    expect(bookingStatusSchema.safeParse({ status: "deleted" }).success).toBe(false);
  });
});
