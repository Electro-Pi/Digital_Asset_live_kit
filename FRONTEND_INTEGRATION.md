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

## 2. Registering RPC Handlers in Next.js / React

In your voice widget component (e.g. `VoiceWidget.tsx`), after `room.connect(...)` succeeds and before setting the microphone to listening:

```tsx
import { useRouter } from "next/navigation";

// Helper responses
function ok(data: Record<string, unknown> = {}) {
  return JSON.stringify({ ok: true, ...data });
}
function fail(error: string) {
  return JSON.stringify({ ok: false, error });
}

export function setupAlawalyVoiceBridge(room: any, router: any) {
  const participant = room.localParticipant;

  // 1. Navigation RPC
  participant.registerRpcMethod("alawaly.navigate", async (data: any) => {
    try {
      const { path, title } = JSON.parse(data.payload) as { path: string; title?: string };
      console.log("[VoiceAgent] Navigating to:", path, title);
      
      if (path.startsWith("http://") || path.startsWith("https://")) {
        window.open(path, "_blank");
      } else {
        router.push(path);
      }
      return ok({ path });
    } catch (e: any) {
      return fail(e.message || "navigation_failed");
    }
  });

  // 2. Show Specific Project
  participant.registerRpcMethod("alawaly.showProject", async (data: any) => {
    try {
      const { projectId, path, title } = JSON.parse(data.payload) as {
        projectId: string;
        path: string;
        title?: string;
      };
      const targetPath = path || `/ar/project/${projectId}`;
      console.log("[VoiceAgent] Showing project:", projectId, targetPath);
      router.push(targetPath);
      return ok({ projectId, path: targetPath });
    } catch (e: any) {
      return fail(e.message || "project_view_failed");
    }
  });

  // 3. Pre-fill Contact & Interest Form
  participant.registerRpcMethod("alawaly.fillContact", async (data: any) => {
    try {
      const payload = JSON.parse(data.payload);
      console.log("[VoiceAgent] Pre-filling contact form:", payload);
      // Dispatch custom event or set form state
      window.dispatchEvent(new CustomEvent("alawaly:fill-contact", { detail: payload }));
      return ok({ filled: true });
    } catch (e: any) {
      return fail(e.message || "fill_contact_failed");
    }
  });

  // 4. Report UI State
  participant.registerRpcMethod("alawaly.getUiState", async () => {
    return ok({
      currentPath: window.location.pathname,
      hash: window.location.hash,
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
