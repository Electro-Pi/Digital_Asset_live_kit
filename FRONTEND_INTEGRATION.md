# Alawaly Real Estate Voice Assistant Frontend Integration (العوالي العقارية)

This document describes how the frontend on **https://www.alawaly.sa/ar** connects to the **Alawaly Real Estate Voice Assistant** over **LiveKit WebRTC RPC**.

---

## 1. Architecture & Low Latency Design

The assistant runs as a LiveKit agent worker connected to the room. Communication between the user's browser and the voice agent happens in two bidirectional channels over a single WebRTC connection:
1. **Audio Track (WebRTC)**:
   - **User Speech -> STT (Cohere / Coda ASR)** with Silero VAD + Turn Detector (end of speech detected in ~350-450ms).
   - **Streaming LLM -> TTS (VoxCPM2 / Coda-V2)**: The model streams tokens immediately; the phrase tokenizer sends the first clause (~4-6 words) to TTS right away, delivering **Time-to-First-Audio (TTFA) under 600-900ms**.
2. **LiveKit RPC (Data Channel)**:
   - The voice agent directly controls the user's browser UI by calling registered RPC methods on `room.localParticipant`.
   - Actions execute in real-time (<50ms) without HTTP polling or extra sockets.

---

## 2. RPC contract

`data.payload` is a JSON string. Parse it. Reply with a JSON string.

| Method | Receives | Returns |
| :--- | :--- | :--- |
| `alawaly.navigate` | `{ "url": "https://www.alawaly.sa/ar/projects" }` | `{ "ok": true, "url": "..." }` |
| `alawaly.showProject` | `{ "projectId": "akam-homes", "url": "https://www.alawaly.sa/ar/project/akam-homes", "name": "آكام هومز" }` | `{ "ok": true, "projectId": "akam-homes", "url": "..." }` |
| `alawaly.fillContact` | interest-form fields, any of them optional | `{ "ok": true }` |
| `alawaly.getUiState` | `{}` | `{ "ok": true, "url": "...", "projectId": "akam-homes" }` |

`alawaly.fillContact` keys, only the ones the caller said: `name`, `phone`, `identityType`, `purchasePlan`, `employer`, `email`, `sakaniEligible`, `monthlyIncome`, `projectId`. Office projects may also send `spaceType`, `companySize`, `purpose`.

```tsx
function ok(data: Record<string, unknown> = {}) {
  return JSON.stringify({ ok: true, ...data });
}
function fail(error: string) {
  return JSON.stringify({ ok: false, error });
}

function go(router: any, url: string) {
  const target = new URL(url);
  if (target.origin === window.location.origin) {
    router.push(target.pathname + target.search + target.hash);
  } else {
    window.location.assign(url);
  }
}

export function setupAlawalyVoiceBridge(room: any, router: any) {
  const participant = room.localParticipant;

  participant.registerRpcMethod("alawaly.navigate", async (data: any) => {
    const { url } = JSON.parse(data.payload) as { url: string };
    go(router, url);
    return ok({ url });
  });

  participant.registerRpcMethod("alawaly.showProject", async (data: any) => {
    const body = JSON.parse(data.payload) as { projectId: string; url: string; name?: string };
    go(router, body.url);
    return ok({ projectId: body.projectId, url: body.url });
  });

  participant.registerRpcMethod("alawaly.fillContact", async (data: any) => {
    const fields = JSON.parse(data.payload);
    window.dispatchEvent(new CustomEvent("alawaly:fill-contact", { detail: fields }));
    return ok();
  });

  participant.registerRpcMethod("alawaly.getUiState", async () => {
    return ok({
      url: window.location.href,
      projectId: document.body.dataset.projectId || null,
    });
  });
}
```

---

## 3. Supported Page Routes on https://www.alawaly.sa/ar

| Page | Path | Description |
| :--- | :--- | :--- |
| **الرئيسية (Home)** | `/ar` | Official homepage of Alawaly |
| **عن العوالي (About Us)** | `/ar/about` | History since 2011, CEO Sulaiman Al-Rumaih, Vision 2030, +16B SAR portfolio |
| **مشاريعنا (Projects)** | `/ar/projects` | Full projects gallery with filters |
| **آكام هومز** | `/ar/project/akam-homes` | Residential apartments in North Jeddah (750 units, 206.84m²) |
| **العوالي هومز – التيسير** | `/ar/project/alawaly-homes-al-taysir-jeddah` | 100% Ready residential apartments (227m²), Real Estate Asia Award winner |
| **آكام جدة هايتس** | `/ar/project/akam-jeddah-heights` | 152 luxury villas with NHC partnership in Al-Hadaba |
| **العوالي رايز** | `/ar/project/alawaly-rise` | Commercial tower in Riyadh (106,000m² on King Fahd / Olaya) |
| **بولاريس** | `/ar/project/polaris` | Luxury residences & penthouses with private pool in North Riyadh |
| **مركز العوالي للأعمال** | `/ar/project/alawaly-business-center` | Salmanian modern offices in Al-Sahafa with Mada partnership |
| **العوالي هومز – المروة** | `/ar/project/alawaly-homes-al-marwa` | 100% Ready residential in Al-Marwa, Jeddah with roof terrace |
| **العوالي تاور** | `/ar/project/alawaly-tower` | 45-storey iconic Salmanian tower in Riyadh with 7 smart parking towers |
| **الخريطة التفاعلية** | `https://alawaly-project.vercel.app/` | 3D Interactive Map |
| **العناية بالعملاء** | `/ar/contact` | Customer care, 920016488, 8003050004, WhatsApp |
| **قنوات التواصل** | `/ar/contact#Communication-channels` | Phone & WhatsApp channels |
| **تطبيق العوالي** | `/ar/contact#App` | App Store & Google Play links |
| **المركز الإعلامي** | `/ar/news` | Latest company news, Paris summit, London exhibition |
| **سجل اهتمامك** | `#contact-section` | Lead capture & booking form |

---

## 4. Voice Agent Capabilities

- **Automatic Briefing**: When the call connects, the assistant immediately introduces itself:
  > «مرحباً بك في العوالي العقارية، أنا مساعدك الذكي. شركة العوالي العقارية رائدة في التطوير العقاري في المملكة منذ عام 2011، نقدم مشاريع سكنية وتجارية نوعية في جدة والرياض وعدة مدن. كيف أقدر أخدمك اليوم؟»
- **Seamless Page Navigation**: Drives the screen on voice command and synchronizes conversation state with UI.
- **Deep Company Knowledge**: When landing on `/ar/about`, speaks freely and authoritatively about the company's track record, leadership, Vision 2030 alignment, and achievements.
- **Accurate Project Specs**: Instant factual answers on unit dimensions, bedrooms, landmarks travel times, completion status, and Sakani eligibility.
