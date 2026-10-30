import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Billing API (T-083): клиент не собирает success/cancel URL и не шлёт
 * цену — только POST без тела; сервер возвращает готовый Stripe URL.
 */

let fetchMock: ReturnType<typeof vi.fn>;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.resetModules();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api: Stripe Checkout и Customer Portal", () => {
  it("createCheckoutSession шлёт POST без тела и возвращает url", async () => {
    const { createCheckoutSession } = await import("./api");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ url: "https://checkout.stripe.test/cs_1" }),
    );

    const result = await createCheckoutSession();

    expect(result).toEqual({ url: "https://checkout.stripe.test/cs_1" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [
      string,
      RequestInit | undefined,
    ];
    expect(url).toBe("/v1/billing/create-checkout-session");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeUndefined();
    expect(init?.credentials).toBe("include");
  });

  it("createPortalSession шлёт POST на portal-эндпоинт", async () => {
    const { createPortalSession } = await import("./api");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ url: "https://billing.stripe.test/portal" }),
    );

    const result = await createPortalSession();

    expect(result.url).toBe("https://billing.stripe.test/portal");
    const [url, init] = fetchMock.mock.calls[0] as [
      string,
      RequestInit | undefined,
    ];
    expect(url).toBe("/v1/billing/create-portal-session");
    expect(init?.method).toBe("POST");
  });

  it("пробрасывает detail/code ошибки Stripe как ApiError", async () => {
    const { createCheckoutSession } = await import("./api");
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          detail: "Оплата временно недоступна: Stripe не настроен",
          code: "billing_not_configured",
        },
        503,
      ),
    );

    await expect(createCheckoutSession()).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
      code: "billing_not_configured",
    });
  });
});
