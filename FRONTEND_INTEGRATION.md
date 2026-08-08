# Frontend work needed for لقمة (voice → UI control)

The agent backend is done and deployed. It can search the catalog, decide on a
meal, and explain the choice on its own. What it **cannot** do until the
frontend ships the code below is move the UI: open a meal page, add to cart, or
read the cart back.

Nothing here changes the voice pipeline or the token route — that all works
today. This is four RPC handlers on the LiveKit `Room` you already connect.

---

## 1. How the agent talks to the browser

Not HTTP, not gRPC. The agent already has an authenticated WebRTC session with
this exact browser tab — the voice call — and LiveKit exposes request/response
RPC on it. The agent calls a method, your handler runs, and whatever you return
goes back to the agent as the tool result.

The return value is the point. If your handler is missing or fails, لقمة is
told the action failed and will say so out loud instead of claiming it opened a
page it never opened. It is under explicit instruction not to fake actions, and
that only works if it hears the truth back from you.

```
agent tool  →  perform_rpc(method, payload)  →  your registered handler
                                             ←  your JSON response
```

**Current behaviour without this work:** every UI call returns
`frontend_handler_missing`, the agent logs an error naming the method, and
لقمة keeps talking normally — it just cannot drive the screen. So you can ship
these handlers one at a time.

---

## 2. Register the handlers

In [`components/voice-widget/VoiceWidget.tsx`](../jahez-frontend/components/voice-widget/VoiceWidget.tsx),
after `room.connect(...)` succeeds and **before** you `setPhase("listening")`.

Register on `room.localParticipant`. Handlers must be registered on the *local*
participant of the browser side — that is who the agent addresses.

```ts
import { useRouter } from "next/navigation";
import { useCart } from "@/lib/cart-context";
import catalog from "@/data/catalog.json";

// inside the component
const router = useRouter();
const { addItem, items } = useCart();

// after `await room.connect(...)`
function ok(data: Record<string, unknown> = {}) {
  return JSON.stringify({ ok: true, ...data });
}
function fail(error: string) {
  return JSON.stringify({ ok: false, error });
}

room.localParticipant.registerRpcMethod("luqma.navigate", async (data) => {
  const { path } = JSON.parse(data.payload) as { path: string };
  if (!path.startsWith("/")) return fail("bad_path");
  router.push(path);
  return ok({ path });
});

room.localParticipant.registerRpcMethod("luqma.showMeal", async (data) => {
  const { mealId, path } = JSON.parse(data.payload) as {
    mealId: string;
    path: string;
    reason?: string;
  };
  router.push(path || `/meals/${mealId}`);
  return ok({ path: path || `/meals/${mealId}` });
});

room.localParticipant.registerRpcMethod("luqma.addToCart", async (data) => {
  const { mealId, quantity, spicy, addonIds } = JSON.parse(data.payload) as {
    mealId: string;
    quantity: number;
    spicy: boolean;
    addonIds: string[];
  };
  // …resolve the meal + addons from the catalog, then call addItem(...)
  // (see section 4 — shape it to match your AddToCartInput)
  return ok({ cartCount: /* new count */ 0 });
});

room.localParticipant.registerRpcMethod("luqma.getCart", async () => {
  return ok({
    count: items.length,
    items: items.map((i) => ({
      mealId: i.mealId,
      quantity: i.quantity,
      unitPrice: i.unitPrice,
    })),
  });
});
```

### Two React gotchas

- **Stale closures.** These handlers are registered once but read `items` and
  `addItem`. If they close over the first render's values, `luqma.getCart` will
  keep reporting an empty cart forever. Keep the live values in a ref
  (`const cartRef = useRef(items); cartRef.current = items;`) and read
  `cartRef.current` inside the handler, or re-register when they change.
- **Unregister on disconnect**, next to your existing `clearAudioElements()`:
  `room.localParticipant.unregisterRpcMethod("luqma.navigate")` etc. Otherwise a
  reconnect stacks duplicate handlers.

---

## 3. The four methods

Every payload includes `"v": 1` (protocol version). Ignore it, or log it if it
is ever not `1`.

Return `{"ok": true, ...}` on success, `{"ok": false, "error": "..."}` on
failure. Anything else is treated as a failure. **Respond within 5 seconds** —
the agent is mid-sentence while it waits, and after 5s it gives up and tells
the user it could not do it.

### `luqma.navigate`
Open one of the app's main pages.
```jsonc
// payload
{ "path": "/cart", "v": 1 }
// response
{ "ok": true, "path": "/cart" }
```
`path` is always one of: `/`, `/cart`, `/orders`, `/offers`, `/search`, `/saved`.

### `luqma.showMeal`
Open a meal's page. This is the one that fires most — لقمة calls it the moment
it settles on a recommendation, so the user sees the dish while hearing why.
```jsonc
// payload
{ "mealId": "madhaq-alsham-hummus", "path": "/meals/madhaq-alsham-hummus",
  "reason": "سعراته ٣٩٠ بس وفيه لحم", "v": 1 }
// response
{ "ok": true, "path": "/meals/madhaq-alsham-hummus" }
```
`path` is prebuilt from your existing `app/meals/[id]` route — just push it.
`reason` is a short Arabic line you may display (a toast, or a highlight on the
page). Optional; ignoring it is fine.

### `luqma.addToCart`
```jsonc
// payload
{ "mealId": "sushi-noi-gyoza", "quantity": 1, "spicy": false,
  "addonIds": ["fries", "cola"], "v": 1 }
// response
{ "ok": true, "cartCount": 3 }
```
Guarantees from the backend, so you do not need to re-check them:
- `mealId` always exists in the catalog.
- `addonIds` only ever contains addon ids that belong to **that** meal —
  invented ones are dropped before the call and reported separately.
- `quantity` ≥ 1; `spicy` is only `true` when the meal has `spicyOption`.

The agent already told the user the line total, computed as
`(price + Σ addon prices) × quantity` using `offerPrice` when the meal is on
offer. Match that in `addItem` or the spoken total and the screen will disagree.

### `luqma.getCart`
```jsonc
// payload
{ "v": 1 }
// response
{ "ok": true, "count": 2,
  "items": [{ "mealId": "sushi-noi-gyoza", "quantity": 1, "unitPrice": 42 }] }
```
Called before لقمة says anything about the cart. If it fails, it says it cannot
see the cart rather than guessing.

---

## 4. Wiring `addToCart` to `cart-context`

Your [`AddToCartInput`](../jahez-frontend/lib/cart-context.tsx) needs full addon
objects, but the agent sends ids. Resolve them from the catalog:

```ts
const meal = catalog.restaurants
  .flatMap((r) => r.meals)
  .find((m) => m.id === mealId);
if (!meal) return fail("unknown_meal");

const addons = (meal.addons ?? []).filter((a) => addonIds.includes(a.id));
addItem({
  mealId: meal.id,
  quantity,
  spicy,
  addons,
  unitPrice: meal.isOffer && meal.offerPrice ? meal.offerPrice : meal.price,
});
```

**Note:** each `addToCart` creates a new cart line. The agent is prompted to
collect addons *before* adding so this happens once, and it warns itself if it
adds the same meal twice in one call — but if you want "add fries to the line
that's already there", that needs an `updateCartItem` method we have not
specced.

---

## 5. The duplicated catalog — please fix this

`catalog.json` now exists twice:

- `jahez-frontend/data/catalog.json` (what the UI renders)
- `coda-jahez-agent-backend/resturant's-catalog/catalog.json` (what the agent searches)

They are identical today. When they drift, لقمة will confidently recommend a
meal whose id 404s in your router — and it will look like an AI bug, not a data
bug, so it will cost hours to find.

Pick one:
1. One file, symlinked or copied by a build step (simplest).
2. Backend serves it and the frontend fetches at build time.
3. Keep both, add a CI check comparing SHA-256 so drift fails loudly.

Option 3 is a five-line script and is enough.

---

## 6. Optional, but nice

- **Show what it's doing.** The `reason` in `showMeal` is written to be shown.
  A small "لقمة اختارت لك هذي" banner makes the voice feel connected to the UI.
- **Send the current page to the agent.** Not implemented on either side. If
  you want لقمة to answer "what am I looking at?", the agent would need a
  `getUiState` equivalent in reverse — ask and it can be added backend-side.
- **Preferences.** Already working — `buildParticipantMetadata()` in
  `VoiceWidget.tsx` ships `prefs` and the agent injects them into its context.
  New onboarding questions flow through automatically as long as each answer
  carries a `label_ar`; no backend change needed.

---

## 7. Testing without voice

`tests/test_luqma_flow.py` in the backend runs whole conversations in text with
a `FakeUI` that mimics these four handlers, so you can see the exact payload
sequence a real call produces:

```bash
cd coda-jahez-agent-backend && .venv/bin/python tests/test_luqma_flow.py
```

Once your handlers are live, watch them arrive:

```bash
sudo journalctl -u jahez-agent.service -f | grep -E "Tool |UI call"
```

A working `showMeal` logs `UI call ok method=luqma.showMeal`. A missing one logs
`UI method luqma.showMeal is not registered by the frontend`.
