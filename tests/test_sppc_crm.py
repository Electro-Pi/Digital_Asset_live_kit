from src.sppc_crm import (
    TicketSpeechStream,
    clean_name,
    normalize_priority,
    normalize_type,
    speak_ticket_numbers,
    spoken_digits,
)

SERIAL_1043 = "وَاحِد صِفْر أَرْبَعَة ثَلَاثَة"


def test_spoken_digits_reads_each_digit():
    assert spoken_digits("1042") == "وَاحِد صِفْر أَرْبَعَة اثْنِين"


def test_ticket_number_is_spoken_as_serial_digits():
    assert speak_ticket_numbers("رقم البلاغ SPPC-26-1043، والفريق راح يتابع.") == (
        f"رقم البلاغ {SERIAL_1043}، والفريق راح يتابع."
    )


def test_ticket_number_variants_are_caught():
    for written in ("sppc 26 1043", "SPPC-٢٦-١٠٤٣", "SPPC – 26 – 1043", "SPPC261043"):
        assert speak_ticket_numbers(f"رقمك {written}.") == f"رقمك {SERIAL_1043}.", written


def test_bare_serial_of_this_calls_ticket_is_spoken():
    assert speak_ticket_numbers("رقمك 1043.", ["1043"]) == f"رقمك {SERIAL_1043}."
    assert speak_ticket_numbers("سعة 21043 ميجاواط", ["1043"]) == "سعة 21043 ميجاواط"


def test_stream_rewrites_a_number_split_across_deltas():
    stream = TicketSpeechStream([])
    out = "".join(stream.feed(d) for d in ["تم التسجيل، رقم البلاغ SP", "PC-26-10", "43. تبي شي ثاني"])
    out += stream.flush()
    assert out == f"تم التسجيل، رقم البلاغ {SERIAL_1043}. تبي شي ثاني"


def test_stream_sees_serials_added_after_it_started():
    serials: list[str] = []
    stream = TicketSpeechStream(serials)
    serials.append("1043")
    assert stream.feed("رقمك 1043.") == f"رقمك {SERIAL_1043}."


def test_clean_name_treats_refusals_as_unknown():
    assert clean_name(None) is None
    assert clean_name("null") is None
    assert clean_name("غير معروف") is None
    assert clean_name("  مُحَمَّد ") == "محمد"


def test_normalizers_accept_arabic_and_fall_back():
    assert normalize_type("مقترح") == "suggestion"
    assert normalize_type("whatever") == "complaint"
    assert normalize_priority("عالي") == "high"
    assert normalize_priority("") == "medium"


def test_ticket_without_content_has_no_substance():
    from src.sppc_crm import has_substance

    # The real ticket from the call where the suggestion was never stated.
    assert not has_substance("تقديم مقترح", "تقديم مقترح")
    assert not has_substance("شكوى", "المتصل يود تقديم شكوى للشركة")
    assert has_substance("تأخير في رد خدمة العملاء", "التأخير في رد خدمة العملاء وصعوبة التواصل معهم")
    assert has_substance("تنبيهات طلبات العروض", "إضافة تنبيهات بالرسائل النصية عند طرح طلب عروض جديد")


def test_leaked_reasoning_channel_is_removed():
    from src.sppc_crm import ModelTokenFilter, strip_model_tokens

    assert strip_model_tokens("<|channel>thought\n<channel|>") == ""
    assert strip_model_tokens("<|channel>thought\n<channel|>تم تسجيل مقترحك.") == "تم تسجيل مقترحك."
    stream = ModelTokenFilter()
    parts = ["تم ", "<|cha", "nnel>thou", "ght\n<chan", "nel|>", "تسجيل <|im_end|>مقترحك."]
    assert "".join(stream.feed(p) for p in parts) + stream.flush() == "تم تسجيل مقترحك."
    assert strip_model_tokens("السعة < 100 ميجاواط") == "السعة < 100 ميجاواط"
