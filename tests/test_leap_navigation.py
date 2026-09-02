import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from livekit import rtc
from livekit.agents import ToolError, llm

from agent import (
    Assistant,
    NavigatorAssistant,
    register_navigator_narration_handler,
)
from src.arabic_text import normalize_arabic_speech_codes
from src.leap_navigation import (
    NAVIGATOR_LOCATIONS,
    NAVIGATOR_NARRATION_TOPIC,
    NAVIGATOR_ROUTE_TOPIC,
    NavigationNarrationError,
    NavigationLocationError,
    build_navigation_narration_text,
    build_navigation_route_action,
    narration_text_from_data_packet,
    navigator_intro,
    navigator_prompt,
    parse_navigation_narration,
    parse_navigator_session,
    resolve_navigator_location,
)


PDF_CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "navigator-frontend"
    / "src"
    / "data"
    / "pdfPlaces.json"
)
PDF_CATALOG = json.loads(PDF_CATALOG_PATH.read_text(encoding="utf-8"))
EXPECTED_ASSIGNED_BOOTH_COUNT = 842
EXPECTED_PUBLIC_PLACE_COUNT = 233
EXPECTED_DESTINATION_COUNT = 1_075
EXPECTED_PUBLIC_LOCATION_IDS = tuple(
    place["id"] for place in PDF_CATALOG["publicPlaces"]
)
EXPECTED_BOOTH_LOCATION_IDS = tuple(place["id"] for place in PDF_CATALOG["places"])
REPRESENTATIVE_EXACT_PUBLIC_IDS = (
    "registration",
    "main-entrance",
    "help-desk",
    "info-hub",
    "leader-pass-investors",
    "ai-deep-tech-a",
    "prayer-room-east",
    "medical-room-g40",
    "meeting-e3-1",
)


class FakeLocalParticipant:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish_data(self, **kwargs) -> None:
        self.published.append(kwargs)


class FakeRoom:
    def __init__(self) -> None:
        self.local_participant = FakeLocalParticipant()


class FakeEventRoom(FakeRoom):
    def __init__(self) -> None:
        super().__init__()
        self.callbacks: dict[str, object] = {}

    def on(self, event: str):
        def decorator(callback):
            self.callbacks[event] = callback
            return callback

        return decorator


class FakeSession:
    def __init__(self) -> None:
        self.say_calls: list[tuple[str, dict]] = []

    def say(self, text: str, **kwargs):
        self.say_calls.append((text, kwargs))


def narration_payload(language: str = "en", **overrides) -> bytes:
    payload = {
        "version": 1,
        "type": "narrate_navigation_route",
        "language": language,
        "startId": "registration",
        "destinationId": "agriculture-food-tech",
        "distanceMeters": 125,
        "durationMinutes": 3,
        "steps": [
            {"id": "0-1", "instruction": "Walk straight through the concourse."},
            {
                "id": "1-2",
                "instruction": "Turn right at the central hall.",
                "note": "Keep INFO HUB on your left.",
            },
            {
                "id": "arrive",
                "instruction": "AGRICULTURE & FOOD TECH is ahead.",
            },
        ],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class NavigatorMetadataTests(unittest.TestCase):
    def test_requires_explicit_navigator_experience(self) -> None:
        self.assertIsNone(parse_navigator_session(None))
        self.assertIsNone(parse_navigator_session("not json"))
        self.assertIsNone(parse_navigator_session('{"language":"ar"}'))
        self.assertIsNone(parse_navigator_session('{"experience":"coda"}'))

    def test_parses_frontend_language_variants(self) -> None:
        arabic = parse_navigator_session(
            '{"experience":"leap-navigator","language":"ar-SA",'
            '"startId":"registration","destinationId":"agriculture-food-tech"}'
        )
        english = parse_navigator_session(
            '{"experience":"leap-navigator","language":"en-US"}'
        )

        self.assertIsNotNone(arabic)
        self.assertEqual(arabic.language, "ar")
        self.assertEqual(arabic.start_id, "registration")
        self.assertEqual(arabic.destination_id, "agriculture-food-tech")
        self.assertIsNotNone(english)
        self.assertEqual(english.language, "en")

    def test_rejects_noncanonical_or_identical_metadata_endpoints(self) -> None:
        invalid = parse_navigator_session(
            '{"experience":"leap-navigator","startId":"food",'
            '"destinationId":"not-a-place"}'
        )
        same = parse_navigator_session(
            '{"experience":"leap-navigator","startId":"agriculture-food-tech",'
            '"destinationId":"agriculture-food-tech"}'
        )

        self.assertIsNotNone(invalid)
        self.assertIsNone(invalid.start_id)
        self.assertIsNone(invalid.destination_id)
        self.assertIsNotNone(same)
        self.assertEqual(same.start_id, "agriculture-food-tech")
        self.assertIsNone(same.destination_id)

    def test_intro_uses_only_missing_context(self) -> None:
        both = navigator_intro(
            "en",
            start_id="registration",
            destination_id="agriculture-food-tech",
        )
        only_start = navigator_intro("en", start_id="registration")
        only_destination = navigator_intro(
            "en", destination_id="agriculture-food-tech"
        )
        neither = navigator_intro("en")

        self.assertIn("Call set_navigation_route now", both)
        self.assertIn("Do not invent or speak a route step", both)
        self.assertIn("ask only where the visitor wants to go", only_start)
        self.assertIn("ask only where the visitor is now", only_destination)
        self.assertIn("Where are you right now?", neither)

    def test_persona_requires_one_shot_frontend_narration(self) -> None:
        english = navigator_prompt("en")
        arabic = navigator_prompt("ar")
        normalized_english = " ".join(english.split())

        self.assertIn("speak them all in one contiguous narration", normalized_english)
        self.assertIn("Never ask the visitor to say next or repeat", normalized_english)
        self.assertIn("كلها مرة واحدة", arabic)


class NavigatorLocationTests(unittest.TestCase):
    def test_catalog_contains_every_public_place_and_assigned_pdf_booth(self) -> None:
        location_ids = tuple(location.id for location in NAVIGATOR_LOCATIONS)

        self.assertEqual(PDF_CATALOG["publicPlaceCount"], EXPECTED_PUBLIC_PLACE_COUNT)
        self.assertEqual(
            PDF_CATALOG["assignedBoothCount"], EXPECTED_ASSIGNED_BOOTH_COUNT
        )
        self.assertEqual(
            location_ids[:EXPECTED_PUBLIC_PLACE_COUNT],
            EXPECTED_PUBLIC_LOCATION_IDS,
        )
        self.assertEqual(
            location_ids[EXPECTED_PUBLIC_PLACE_COUNT:],
            EXPECTED_BOOTH_LOCATION_IDS,
        )
        self.assertEqual(len(location_ids), EXPECTED_DESTINATION_COUNT)
        self.assertEqual(
            len(location_ids),
            EXPECTED_PUBLIC_PLACE_COUNT + EXPECTED_ASSIGNED_BOOTH_COUNT,
        )
        self.assertEqual(len(set(location_ids)), len(location_ids))
        self.assertTrue(
            set(REPRESENTATIVE_EXACT_PUBLIC_IDS).issubset(location_ids)
        )
        self.assertTrue(
            all(
                location_id.startswith("booth-")
                for location_id in location_ids[EXPECTED_PUBLIC_PLACE_COUNT:]
            )
        )

    def test_resolves_ids_names_and_declared_aliases(self) -> None:
        self.assertEqual(resolve_navigator_location("main-entrance").id, "main-entrance")
        self.assertEqual(
            resolve_navigator_location("AGRICULTURE & FOOD TECH").id,
            "agriculture-food-tech",
        )
        self.assertEqual(resolve_navigator_location("مكتب المساعدة").id, "help-desk")
        self.assertEqual(
            resolve_navigator_location("دَوْرَات مِيَاه النساء · الغربية").id,
            "toilet-female-west",
        )

        for location in NAVIGATOR_LOCATIONS[:EXPECTED_PUBLIC_PLACE_COUNT]:
            # Canonical IDs and bilingual full names resolve exactly unless the
            # same full printed name genuinely labels another destination. T2 is
            # both a public place and the exhibitor name at booth H2.Q10.
            if location.id == "t2":
                continue
            labels = (location.id, location.name, location.name_ar)
            for label in labels:
                with self.subTest(location=location.id, label=label):
                    self.assertEqual(
                        resolve_navigator_location(label).id,
                        location.id,
                    )

        self.assertEqual(resolve_navigator_location("t2").id, "t2")
        with self.assertRaisesRegex(NavigationLocationError, "H2.Q10"):
            resolve_navigator_location("T2")

        pdf_locations = NAVIGATOR_LOCATIONS[EXPECTED_PUBLIC_PLACE_COUNT:]
        for location in pdf_locations:
            with self.subTest(location=location.id):
                self.assertEqual(resolve_navigator_location(location.id).id, location.id)
                booth_code = next(
                    alias for alias in location.aliases if alias.startswith("H")
                )
                self.assertEqual(resolve_navigator_location(booth_code).id, location.id)

        self.assertEqual(resolve_navigator_location("RedFrog").id, "booth-h3-h150")
        with self.assertRaises(NavigationLocationError):
            resolve_navigator_location("STC")
        with self.assertRaises(NavigationLocationError):
            resolve_navigator_location("CATERING")

    def test_resolves_natural_speech_and_conservative_asr_variants(self) -> None:
        self.assertEqual(
            resolve_navigator_location("please take me to info hub").id,
            "info-hub",
        )
        self.assertEqual(
            resolve_navigator_location("I want Microsoft").id,
            "booth-h1-e30",
        )
        self.assertEqual(
            resolve_navigator_location("ودني مايكروسوفت").id,
            "booth-h1-e30",
        )
        self.assertEqual(
            resolve_navigator_location("والدين إلى مايكروسوفت").id,
            "booth-h1-e30",
        )
        self.assertEqual(resolve_navigator_location("أبي أروح جوجل").id, "booth-h1-j30")
        self.assertEqual(resolve_navigator_location("ودني سيسكو").id, "booth-h1-f30")
        self.assertEqual(resolve_navigator_location("خذني اوراكل").id, "booth-h1-k40")
        self.assertEqual(resolve_navigator_location("infu hub").id, "info-hub")

    def test_does_not_guess_an_unmapped_description(self) -> None:
        with self.assertRaises(NavigationLocationError):
            resolve_navigator_location("the cyber place")

    def test_rejects_same_start_and_destination(self) -> None:
        with self.assertRaises(NavigationLocationError):
            build_navigation_route_action(
                "agriculture-food-tech",
                "AGRICULTURE & FOOD TECH",
                language="en",
            )

    def test_builds_versioned_canonical_action(self) -> None:
        action = build_navigation_route_action(
            "REGISTRATION (CP2)",
            "مكتب المساعدة",
            language="ar",
        )

        self.assertEqual(action["version"], 1)
        self.assertEqual(action["type"], "set_navigation_route")
        self.assertEqual(action["startId"], "registration")
        self.assertEqual(action["destinationId"], "help-desk")
        self.assertEqual(action["language"], "ar")

    def test_builds_route_from_two_natural_spoken_phrases(self) -> None:
        action = build_navigation_route_action(
            "أنا عند مكتب المساعدة",
            "أبغى أروح مايكروسوفت",
            language="ar",
        )

        self.assertEqual(action["startId"], "help-desk")
        self.assertEqual(action["destinationId"], "booth-h1-e30")


class NavigatorNarrationTests(unittest.TestCase):
    def test_normalizes_mixed_place_codes_for_arabic_speech(self) -> None:
        text = normalize_arabic_speech_codes(
            "اتجه من التسجيل CP2 إلى Microsoft Arabia في الجناح H1.E30 ثم G-21."
        )

        self.assertIn("سي بي اثنين", text)
        self.assertIn("إتش واحد إي ثلاثين", text)
        self.assertIn("جي واحد وعشرين", text)
        self.assertNotIn("CP2", text)
        self.assertNotIn("H1.E30", text)
        self.assertNotIn("G-21", text)

    def test_parses_and_renders_every_authoritative_step_in_order(self) -> None:
        narration = parse_navigation_narration(
            narration_payload(),
            expected_language="en",
        )
        text = build_navigation_narration_text(narration)

        self.assertEqual(len(narration.steps), 3)
        self.assertLess(text.index("Walk straight"), text.index("Turn right"))
        self.assertLess(
            text.index("Turn right"),
            text.index("AGRICULTURE & FOOD TECH is ahead"),
        )
        self.assertIn("Keep INFO HUB on your left", text)
        self.assertTrue(
            text.endswith(
                "Do you need anything else, or would you like directions to another "
                "destination?"
            )
        )

    def test_builds_localized_arabic_closing_question(self) -> None:
        payload = narration_payload(
            "ar",
            steps=[
                {"id": "0-1", "instruction": "امش مستقيم في الممر."},
                {
                    "id": "arrive",
                    "instruction": "الزراعة وتقنية الغذاء قدامك.",
                },
            ],
        )
        narration = parse_navigation_narration(payload, expected_language="ar")
        text = build_navigation_narration_text(narration)

        self.assertIn("هذا مسارك كامل", text)
        self.assertIn("الخطوة 1", text)
        self.assertIn("سي بي اثنين", text)
        self.assertNotIn("CP2", text)
        self.assertTrue(text.endswith("ولا تبي تروح لوجهة ثانية؟"))

    def test_rejects_untrusted_geometry_contracts(self) -> None:
        invalid_payloads = (
            narration_payload(version=True),
            narration_payload(language="ar"),
            narration_payload(startId="unknown-booth"),
            narration_payload(destinationId="registration"),
            narration_payload(distanceMeters=1.5),
            narration_payload(steps=[]),
            narration_payload(
                steps=[
                    {"id": str(index), "instruction": "Continue."}
                    for index in range(31)
                ]
            ),
            narration_payload(
                steps=[
                    {"id": "same", "instruction": "First."},
                    {"id": "same", "instruction": "Second."},
                ]
            ),
            narration_payload(steps=[{"id": "x", "instruction": ""}]),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload[:100]):
                with self.assertRaises(NavigationNarrationError):
                    parse_navigation_narration(payload, expected_language="en")

        with self.assertRaises(NavigationNarrationError):
            parse_navigation_narration(b"x" * 14_001, expected_language="en")

    def test_packet_requires_topic_sender_and_reliable_delivery(self) -> None:
        participant = SimpleNamespace(identity="visitor-1")
        valid = rtc.DataPacket(
            data=narration_payload(),
            kind=rtc.DataPacketKind.KIND_RELIABLE,
            participant=participant,
            topic=NAVIGATOR_NARRATION_TOPIC,
        )
        wrong_topic = rtc.DataPacket(
            data=valid.data,
            kind=valid.kind,
            participant=participant,
            topic="unrelated",
        )
        wrong_sender = rtc.DataPacket(
            data=valid.data,
            kind=valid.kind,
            participant=SimpleNamespace(identity="someone-else"),
            topic=valid.topic,
        )
        lossy = rtc.DataPacket(
            data=valid.data,
            kind=rtc.DataPacketKind.KIND_LOSSY,
            participant=participant,
            topic=valid.topic,
        )

        text = narration_text_from_data_packet(
            valid,
            expected_participant_identity="visitor-1",
            expected_language="en",
        )
        self.assertIn("Step 1", text)
        self.assertIsNone(
            narration_text_from_data_packet(
                wrong_topic,
                expected_participant_identity="visitor-1",
                expected_language="en",
            )
        )
        self.assertIsNone(
            narration_text_from_data_packet(
                wrong_sender,
                expected_participant_identity="visitor-1",
                expected_language="en",
            )
        )
        with self.assertRaises(NavigationNarrationError):
            narration_text_from_data_packet(
                lossy,
                expected_participant_identity="visitor-1",
                expected_language="en",
            )


class NavigatorAgentTests(unittest.IsolatedAsyncioTestCase):
    def test_generic_and_navigator_tools_stay_separate(self) -> None:
        generic = Assistant(instructions="generic")
        navigator = NavigatorAssistant(
            instructions=navigator_prompt("en"),
            room=FakeRoom(),
            language="en",
        )

        self.assertEqual([tool.id for tool in generic.tools], ["lookup_weather"])
        self.assertEqual(
            [tool.id for tool in navigator.tools],
            ["set_navigation_route"],
        )

    async def test_tool_publishes_reliable_json_on_route_topic(self) -> None:
        room = FakeRoom()
        navigator = NavigatorAssistant(
            instructions=navigator_prompt("en"),
            room=room,
            language="en",
        )

        result = await navigator.set_navigation_route(
            None,
            current_location="MAIN ENTRANCE",
            destination="PRAYER ROOM · EAST",
        )

        self.assertEqual(result["status"], "route_set")
        self.assertEqual(len(room.local_participant.published), 1)
        publication = room.local_participant.published[0]
        self.assertTrue(publication["reliable"])
        self.assertEqual(publication["topic"], NAVIGATOR_ROUTE_TOPIC)
        payload = json.loads(publication["payload"].decode("utf-8"))
        self.assertEqual(payload["startId"], "main-entrance")
        self.assertEqual(payload["destinationId"], "prayer-room-east")

    async def test_tool_validates_and_remembers_endpoints_one_at_a_time(self) -> None:
        room = FakeRoom()
        navigator = NavigatorAssistant(
            instructions=navigator_prompt("ar"),
            room=room,
            language="ar",
        )

        first = await navigator.set_navigation_route(
            None,
            current_location="أنا عند مكتب المساعدة",
        )
        self.assertEqual(first["status"], "need_destination")
        self.assertEqual(first["startId"], "help-desk")
        self.assertEqual(room.local_participant.published, [])

        second = await navigator.set_navigation_route(
            None,
            destination="أبغى أروح مايكروسوفت",
        )
        self.assertEqual(second["status"], "route_set")
        self.assertEqual(second["startId"], "help-desk")
        self.assertEqual(second["destinationId"], "booth-h1-e30")
        self.assertEqual(len(room.local_participant.published), 1)

        third = await navigator.set_navigation_route(
            None,
            destination="روح بي غرفة الصلاة الشرقية",
        )
        self.assertEqual(third["status"], "route_set")
        self.assertEqual(third["startId"], "help-desk")
        self.assertEqual(third["destinationId"], "prayer-room-east")
        self.assertEqual(len(room.local_participant.published), 2)

    async def test_turn_hook_routes_an_asr_damaged_replacement_destination(self) -> None:
        room = FakeRoom()
        navigator = NavigatorAssistant(
            instructions=navigator_prompt("ar"),
            room=room,
            language="ar",
            start_id="help-desk",
            destination_id="prayer-room-east",
        )
        turn_context = llm.ChatContext.empty()
        message = llm.ChatMessage(
            role="user",
            content=["والدين إلى مايكروسوفت"],
        )

        await navigator.on_user_turn_completed(turn_context, message)

        self.assertEqual(len(room.local_participant.published), 1)
        payload = json.loads(
            room.local_participant.published[0]["payload"].decode("utf-8")
        )
        self.assertEqual(payload["startId"], "help-desk")
        self.assertEqual(payload["destinationId"], "booth-h1-e30")
        self.assertEqual(turn_context.items[-1].role, "system")
        self.assertIn("already", turn_context.items[-1].content[0])

    async def test_invalid_tool_input_does_not_publish(self) -> None:
        room = FakeRoom()
        navigator = NavigatorAssistant(
            instructions=navigator_prompt("en"),
            room=room,
            language="en",
        )

        with self.assertRaises(ToolError):
            await navigator.set_navigation_route(
                None,
                current_location="some booth that is not mapped",
                destination="food",
            )

        self.assertEqual(room.local_participant.published, [])

    async def test_narration_handler_deduplicates_one_interruptible_say_call(self) -> None:
        room = FakeEventRoom()
        session = FakeSession()
        callback = register_navigator_narration_handler(
            room=room,
            session=session,
            participant_identity="visitor-1",
            language="en",
        )
        packet = rtc.DataPacket(
            data=narration_payload(),
            kind=rtc.DataPacketKind.KIND_RELIABLE,
            participant=SimpleNamespace(identity="visitor-1"),
            topic=NAVIGATOR_NARRATION_TOPIC,
        )

        callback(packet)
        callback(packet)

        self.assertEqual(len(session.say_calls), 1)
        narration_text, options = session.say_calls[0]
        self.assertIn("Step 1", narration_text)
        self.assertIn("Step 2", narration_text)
        self.assertIn("Step 3", narration_text)
        self.assertTrue(options["allow_interruptions"])


if __name__ == "__main__":
    unittest.main()
