#!/usr/bin/env python3
"""Extract assigned LEAP booth destinations from the source floor-plan PDF.

The supplied plan was produced by PDFKit and contains selectable text. Poppler's
raw text order preserves the booth/exhibitor directory, while raw bounding boxes
provide each printed booth code's exact page position. This script aligns those
two lossless outputs and writes the static catalog consumed by the navigator.

Usage:
    python3 scripts/extract-pdf-places.py \
      --pdf "/path/to/leap plan.pdf" \
      --output src/data/pdfPlaces.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


PDF_TO_MAP_SCALE = 25 / 12
FIRST_ASSIGNED_BOOTH = "H3.H150"
LAST_ASSIGNED_BOOTH = "H3.I120"
LAST_ASSIGNED_NAME = ("Zone", "Trading")

FULL_BOOTH_CODE = re.compile(r"^H(?:1A|[1-4])\.[A-Z][0-9]+$")
SPLIT_BOOTH_PREFIX = re.compile(r"^H(?:1A|[1-4])\.[A-Z]$")

# A handful of Hall 2 labels are printed diagonally in very narrow stands. The
# PDF stores those words as syllable fragments. These exact transcriptions keep
# the generated catalog readable without changing coordinates or inventing
# exhibitors that are not present in the source.
NAME_OVERRIDES = {
    "H2.B10": "Ministry of Human Resources and Social Development",
    "H2.B30": "Beem Digital Company Ltd",
    "H2.G10": "Ministry of Interior",
    "H2.G30": "Ministry of Justice",
    "H2.H10": "TAWAL · Ookla",
    "H2.H40": "Dawiyat Integrated",
    "H2.I10": "Saudi Arabian Mining Company (MAADEN)",
    "H2.I40": "Saudi Electricity Company",
    "H2.J30": "Systems Arabia",
    "H2.K20": "HOTS Innovations & Technology",
    "H2.K30": "Node Technologies Company",
    "H2.L40": "Veeam Software Group GmbH · NATCOM · Netkit",
    "H2.M10": "ServiceNow Arabia Limited · Customizo · DXC Technology · Plat4mation · RAQMEA",
    "H2.M20": "Devoteam Saudi Arabia Co. Ltd",
    "H2.M40": "OpenText Saudi Arabia LLC",
    "H2.N20": "Branch of Fortinet UK Ltd. · The Tech Rider",
    "H2.O20": "Accenture Saudi Arabia Limited",
    "H2.O30": "Nutanix · VAS by STC · Citrix · AWS · Nahil Computers Company",
    "H2.O40": "Nintex UK Limited · Al Nafitha Diyar",
}


@dataclass(frozen=True)
class Word:
    text: str
    line: int
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x_min + self.x_max) / 2, (self.y_min + self.y_max) / 2)


@dataclass
class Booth:
    code: str
    code_words: list[Word]
    name_words: list[Word]


@dataclass(frozen=True)
class PublicPlaceSpec:
    id: str
    name: str
    name_ar: str
    x: float
    y: float
    zone: str
    zone_ar: str
    source_label: str | None = None
    featured: bool = False


# Visitor-facing labels audited directly against page 1's text objects and
# high-resolution render. Repeated labels are separate physical placements and
# receive a suffix only to make them unambiguous in the planner. Named staff and
# technical rooms remain searchable because the requested catalog is complete;
# fixture annotations (W.C., DB, FHC, panels, fire equipment), the unreadable
# G-172 label, and unassigned booth codes are not named destinations.
PUBLIC_PLACE_SPECS: tuple[PublicPlaceSpec, ...] = (
    # Access and arrival
    PublicPlaceSpec("royal-entrance-courtyard", "ROYAL ENTRANCE COURTYARD", "ساحة المدخل الملكي", 616.7, 318.6, "Access", "المداخل"),
    PublicPlaceSpec("royal-entrance-g199", "ROYAL ENTRANCE (G-199)", "المدخل الملكي (G-199)", 630.9, 299.7, "Access", "المداخل", "ROYAL ENTRANCE"),
    PublicPlaceSpec("access-hall-1", "ACCESS TO HALL 1", "مدخل الصالة 1", 874.9, 802.9, "Access", "المداخل", featured=True),
    PublicPlaceSpec("access-hall-2-a", "ACCESS TO HALL 2 · A", "مدخل الصالة 2 · أ", 841.2, 788.3, "Access", "المداخل", "ACCESS TO HALL 2"),
    PublicPlaceSpec("access-hall-2-b", "ACCESS TO HALL 2 · B", "مدخل الصالة 2 · ب", 945.9, 646.4, "Access", "المداخل", "ACCESS TO HALL 2"),
    PublicPlaceSpec("access-hall-2-c", "ACCESS TO HALL 2 · C", "مدخل الصالة 2 · ج", 917.8, 617.0, "Access", "المداخل", "ACCESS TO HALL 2"),
    PublicPlaceSpec("main-entrance", "MAIN ENTRANCE", "المدخل الرئيسي", 965.9, 751.2, "Access", "المداخل", featured=True),
    PublicPlaceSpec("traffic-security-checkpoint", "TRAFFIC & SECURITY CHECKPOINT", "نقطة تفتيش المرور والأمن", 314.8, 195.4, "Access", "المداخل"),
    PublicPlaceSpec("security-scanners-a", "SECURITY SCANNERS · A", "أجهزة التفتيش الأمني · أ", 796.4, 368.8, "Access", "المداخل", "SECURITY SCANNERS"),
    PublicPlaceSpec("security-scanners-b", "SECURITY SCANNERS · B", "أجهزة التفتيش الأمني · ب", 1032.1, 603.4, "Access", "المداخل", "SECURITY SCANNERS"),
    PublicPlaceSpec("security-scanners-c", "SECURITY SCANNERS · C", "أجهزة التفتيش الأمني · ج", 566.9, 206.9, "Access", "المداخل", "SECURITY SCANNERS"),
    PublicPlaceSpec("vip-registration", "VIP REG", "تسجيل كبار الشخصيات", 346.8, 1114.9, "Arrival", "الوصول"),
    PublicPlaceSpec("vip-access", "VIP ACCESS ONLY", "دخول كبار الشخصيات فقط", 356.3, 1136.0, "Arrival", "الوصول"),
    PublicPlaceSpec("registration", "REGISTRATION (CP2)", "التسجيل (CP2)", 317.9, 1351.1, "Arrival", "الوصول", featured=True),
    PublicPlaceSpec("cp2-entrance-a", "CP2 ENTRANCE · A", "مدخل CP2 · أ", 339.9, 1330.7, "Arrival", "الوصول", "ENTRANCE"),
    PublicPlaceSpec("cp2-entrance-b", "CP2 ENTRANCE · B", "مدخل CP2 · ب", 340.7, 1323.7, "Arrival", "الوصول", "ENTRANCE"),
    PublicPlaceSpec("cp2-exit-a", "CP2 EXIT · A", "مخرج CP2 · أ", 344.1, 1313.7, "Arrival", "الوصول", "EXIT"),
    PublicPlaceSpec("cp2-exit-b", "CP2 EXIT · B", "مخرج CP2 · ب", 344.5, 1309.3, "Arrival", "الوصول", "EXIT"),
    PublicPlaceSpec("south-entrance-west", "ENTRANCE · SOUTH WEST", "المدخل الجنوبي الغربي", 432.1, 1138.8, "South entrances", "المداخل الجنوبية", "ENTRANCE"),
    PublicPlaceSpec("south-entrance-centre", "ENTRANCE · SOUTH CENTRE", "المدخل الجنوبي الأوسط", 634.4, 1138.8, "South entrances", "المداخل الجنوبية", "ENTRANCE"),
    PublicPlaceSpec("south-entrance-east", "ENTRANCE · SOUTH EAST", "المدخل الجنوبي الشرقي", 838.5, 1138.8, "South entrances", "المداخل الجنوبية", "ENTRANCE"),
    PublicPlaceSpec("exit-only-west", "EXIT ONLY · WEST", "مخرج فقط · الغربي", 522.9, 1126.6, "South entrances", "المداخل الجنوبية", "EXIT ONLY"),
    PublicPlaceSpec("exit-only-east", "EXIT ONLY · EAST", "مخرج فقط · الشرقي", 742.9, 1126.2, "South entrances", "المداخل الجنوبية", "EXIT ONLY"),

    # Visitor services
    PublicPlaceSpec("leap-nights-a", "LEAP NIGHTS · A", "ليالي ليب · أ", 942.4, 769.2, "Visitor services", "خدمات الزوار", "LEAP NIGHTS"),
    PublicPlaceSpec("leap-nights-b", "LEAP NIGHTS · B", "ليالي ليب · ب", 916.3, 774.0, "Visitor services", "خدمات الزوار", "LEAP NIGHTS"),
    PublicPlaceSpec("speakers", "SPEAKERS", "المتحدثون", 900.6, 736.7, "Visitor services", "خدمات الزوار"),
    PublicPlaceSpec("help-desk", "HELP DESK", "مكتب المساعدة", 901.3, 737.4, "Visitor services", "خدمات الزوار", featured=True),
    PublicPlaceSpec("info-hub", "INFO HUB", "مركز المعلومات", 907.7, 721.6, "Visitor services", "خدمات الزوار", featured=True),
    PublicPlaceSpec("customer-service-a", "CUSTOMER SERVICE · A", "خدمة العملاء · أ", 926.8, 711.0, "Visitor services", "خدمات الزوار", "CUSTOMER SERVICE"),
    PublicPlaceSpec("customer-service-b", "CUSTOMER SERVICE · B", "خدمة العملاء · ب", 922.5, 715.2, "Visitor services", "خدمات الزوار", "CUSTOMER SERVICE"),
    PublicPlaceSpec("registration-service", "REGISTRATION · SERVICE DESK", "التسجيل · مكتب الخدمة", 929.5, 743.6, "Visitor services", "خدمات الزوار", "REGISTRATION"),
    PublicPlaceSpec("lost-found", "LOST & FOUND", "المفقودات", 905.3, 732.6, "Visitor services", "خدمات الزوار"),
    PublicPlaceSpec("leap-app", "LEAP APP", "تطبيق ليب", 918.3, 719.6, "Visitor services", "خدمات الزوار"),
    PublicPlaceSpec("ges", "GES", "GES", 913.6, 723.6, "Visitor services", "خدمات الزوار"),
    PublicPlaceSpec("dsv", "DSV", "DSV", 909.2, 728.0, "Visitor services", "خدمات الزوار"),
    PublicPlaceSpec("leader-pass-investors", "LEADER PASS & INVESTORS", "بطاقة القادة والمستثمرون", 896.6, 741.4, "Visitor services", "خدمات الزوار", featured=True),

    # Thematic zones (source spelling preserved, including SOONICORNS)
    PublicPlaceSpec("ecosystem-partners", "ECOSYSTEM PARTNERS", "شركاء المنظومة", 684.4, 936.7, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("saudi-soonicorns", "SAUDI SOONICORNS", "الشركات السعودية الصاعدة", 618.2, 936.7, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("saudi-enablers", "SAUDI ENABLERS", "الممكّنون السعوديون", 637.6, 831.6, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("global-unicorns", "GLOBAL UNICORNS", "الشركات المليارية العالمية", 565.4, 914.4, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("financial-services-fintech", "FINANCIAL SERVICES & FINTECH", "الخدمات المالية والتقنية المالية", 583.2, 958.1, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("agriculture-food-tech", "AGRICULTURE & FOOD TECH", "الزراعة وتقنية الغذاء", 565.5, 936.8, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("ai-deep-tech-a", "AI & DEEP TECH · A", "الذكاء الاصطناعي والتقنية العميقة · أ", 557.7, 873.1, "Thematic zones", "المناطق المتخصصة", "AI & DEEP TECH", featured=True),
    PublicPlaceSpec("ai-deep-tech-b", "AI & DEEP TECH · B", "الذكاء الاصطناعي والتقنية العميقة · ب", 599.5, 881.7, "Thematic zones", "المناطق المتخصصة", "AI & DEEP TECH"),
    PublicPlaceSpec("garage", "GARAGE", "ذا جراج", 689.2, 824.3, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("cybersecurity-aerospace-defence", "CYBERSECURITY, AEROSPACE & DEFENCE", "الأمن السيبراني والفضاء والدفاع", 566.4, 850.0, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("smart-living-consumer-tech", "SMART LIVING & CONSUMER TECH", "الحياة الذكية وتقنية المستهلك", 690.2, 889.9, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("education-talent", "EDUCATION & TALENT", "التعليم والمواهب", 684.0, 872.8, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("creative-sports-entertainment", "CREATIVE INDUSTRIES, SPORTS & ENTERTAINMENT", "الصناعات الإبداعية والرياضة والترفيه", 722.9, 824.2, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("biotech-health-life-sciences", "BIOTECH, HEALTH & LIFE SCIENCES", "التقنية الحيوية والصحة وعلوم الحياة", 585.9, 824.8, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("energy-sustainability-utilities", "ENERGY, SUSTAINABILITY & UTILITIES", "الطاقة والاستدامة والمرافق", 691.3, 850.0, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("aviation-automotive", "AVIATION & AUTOMOTIVE", "الطيران والسيارات", 578.6, 890.3, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("manufacturing-logistics", "MANUFACTURING & LOGISTICS", "التصنيع والخدمات اللوجستية", 547.5, 890.3, "Thematic zones", "المناطق المتخصصة"),
    PublicPlaceSpec("hospitality-travel-tourism", "HOSPITALITY, TRAVEL & TOURISM", "الضيافة والسفر والسياحة", 727.0, 864.0, "Thematic zones", "المناطق المتخصصة"),

    # Prayer rooms and toilets
    PublicPlaceSpec("prayer-female-g185", "G-185 FEMALE PRAYER ROOM", "مصلى النساء G-185", 356.9, 221.4, "Prayer rooms", "المصليات", "FEMALE PRAYER ROOM"),
    PublicPlaceSpec("prayer-male-g176", "G-176 MALE PRAYER ROOM", "مصلى الرجال G-176", 356.7, 250.6, "Prayer rooms", "المصليات", "MALE PRAYER ROOM"),
    PublicPlaceSpec("prayer-female-g57", "G-57 FEMALE PRAYER ROOM", "مصلى النساء G-57", 950.8, 521.5, "Prayer rooms", "المصليات", "FEMALE PRAYER ROOM"),
    PublicPlaceSpec("prayer-male-g41", "G-41 MALE PRAYER ROOM", "مصلى الرجال G-41", 978.3, 540.4, "Prayer rooms", "المصليات", "MALE PRAYER ROOM"),
    PublicPlaceSpec("prayer-female-g164", "G-164 FEMALE PRAYER ROOM", "مصلى النساء G-164", 466.8, 219.8, "Prayer rooms", "المصليات", "FEMALE PRAYER ROOM"),
    PublicPlaceSpec("prayer-male-g148", "G-148 MALE PRAYER ROOM", "مصلى الرجال G-148", 499.5, 213.0, "Prayer rooms", "المصليات", "MALE PRAYER ROOM"),
    PublicPlaceSpec("prayer-room-east", "PRAYER ROOM · EAST", "المصلى · الشرقي", 814.7, 1027.2, "Prayer rooms", "المصليات", "PRAYER ROOM", featured=True),
    PublicPlaceSpec("prayer-male-west", "MALE PRAYER ROOM · WEST", "مصلى الرجال · الغربي", 474.3, 1023.1, "Prayer rooms", "المصليات", "MALE PRAYER ROOM"),
    PublicPlaceSpec("prayer-female-west", "FEMALE PRAYER ROOM · WEST", "مصلى النساء · الغربي", 474.3, 921.8, "Prayer rooms", "المصليات", "FEMALE PRAYER ROOM"),
    PublicPlaceSpec("toilet-male-east", "MALE TOILET · EAST", "دورات مياه الرجال · الشرقية", 814.7, 923.8, "Toilets", "دورات المياه", "MALE TOILET"),
    PublicPlaceSpec("toilet-female-east", "FEMALE TOILET · EAST", "دورات مياه النساء · الشرقية", 814.7, 908.2, "Toilets", "دورات المياه", "FEMALE TOILET"),
    PublicPlaceSpec("toilet-male-west", "MALE TOILET · WEST", "دورات مياه الرجال · الغربية", 476.0, 894.7, "Toilets", "دورات المياه", "MALE TOILET"),
    PublicPlaceSpec("toilet-female-west", "FEMALE TOILET · WEST", "دورات مياه النساء · الغربية", 476.0, 907.5, "Toilets", "دورات المياه", "FEMALE TOILET"),

    # Media, medical, lounges, and gates
    PublicPlaceSpec("media-room-g90", "G-90 MEDIA ROOM 2", "غرفة الإعلام 2 (G-90)", 772.1, 337.5, "Media and medical", "الإعلام والخدمات الطبية", "MEDIA ROOM 2"),
    PublicPlaceSpec("medical-room-g40", "G-40 MEDICAL ROOM", "الغرفة الطبية G-40", 1011.6, 578.4, "Media and medical", "الإعلام والخدمات الطبية", "MEDICAL ROOM", featured=True),
    PublicPlaceSpec("media-room-g91", "G-91 MEDIA ROOM 3", "غرفة الإعلام 3 (G-91)", 762.0, 326.9, "Media and medical", "الإعلام والخدمات الطبية", "MEDIA ROOM 3"),
    PublicPlaceSpec("media-room-g92", "G-92 MEDIA ROOM 4", "غرفة الإعلام 4 (G-92)", 749.7, 314.9, "Media and medical", "الإعلام والخدمات الطبية", "MEDIA ROOM 4"),
    PublicPlaceSpec("royal-suite-entourage", "ROYAL SUITE ENTOURAGE, MEDICAL, & STAFF", "جناح المرافقين والطاقم الطبي والموظفين", 654.2, 231.1, "Media and medical", "الإعلام والخدمات الطبية"),
    PublicPlaceSpec("lounge-g119", "G-119 LOUNGE", "استراحة G-119", 659.7, 221.6, "Lounges", "الاستراحات", "LOUNGE"),
    PublicPlaceSpec("media-room-g89", "G-89 MEDIA ROOM 1", "غرفة الإعلام 1 (G-89)", 784.2, 349.6, "Media and medical", "الإعلام والخدمات الطبية", "MEDIA ROOM 1"),
    PublicPlaceSpec("gate-g19-06", "G-19 GATE 06", "البوابة 06 (G-19)", 1038.5, 597.8, "Gates", "البوابات", "GATE 06"),
    PublicPlaceSpec("gate-g62-07", "G-62 GATE 07", "البوابة 07 (G-62)", 941.6, 503.7, "Gates", "البوابات", "GATE 07"),
    PublicPlaceSpec("gate-g88-08", "G-88 GATE 08", "البوابة 08 (G-88)", 803.3, 362.8, "Gates", "البوابات", "GATE 08"),
    PublicPlaceSpec("gate-g143-10", "G-143 GATE 10", "البوابة 10 (G-143)", 566.8, 211.4, "Gates", "البوابات", "GATE 10"),
    PublicPlaceSpec("gate-g118-09", "G-118 GATE 09", "البوابة 09 (G-118)", 678.6, 240.5, "Gates", "البوابات", "GATE 09"),
    PublicPlaceSpec("gate-a24", "GATE A-24", "البوابة A-24", 774.5, 1023.6, "Gates", "البوابات"),
    PublicPlaceSpec("gate-a13", "GATE A-13", "البوابة A-13", 633.7, 1023.0, "Gates", "البوابات"),
    PublicPlaceSpec("gate-a4", "GATE A-4", "البوابة A-4", 508.2, 1023.0, "Gates", "البوابات"),
    PublicPlaceSpec("gate-b14", "GATE B-14", "البوابة B-14", 475.3, 970.6, "Gates", "البوابات"),
    PublicPlaceSpec("gate-b11", "GATE B-11", "البوابة B-11", 475.7, 929.9, "Gates", "البوابات"),
    PublicPlaceSpec("gate-b7", "GATE B-7", "البوابة B-7", 474.9, 875.2, "Gates", "البوابات"),

    # Other exact named places
    PublicPlaceSpec("esm-office-south", "ESM OFFICE · SOUTH", "مكتب ESM · الجنوبي", 559.3, 1029.8, "Offices and operations", "المكاتب والتشغيل", "ESM OFFICE"),
    PublicPlaceSpec("esm-office-north", "ESM OFFICE · NORTH", "مكتب ESM · الشمالي", 535.2, 220.6, "Offices and operations", "المكاتب والتشغيل", "ESM OFFICE"),
    PublicPlaceSpec("ges-vip-waiting", "GES VIP WAITING ROOM", "غرفة انتظار كبار الشخصيات GES", 987.1, 716.8, "Lounges", "الاستراحات"),
    PublicPlaceSpec("qobolak", "QOBOLAK", "QOBOLAK", 152.4, 1053.1, "Other named places", "أماكن أخرى"),
    PublicPlaceSpec("t1", "T1", "T1", 323.3, 1051.3, "Other named places", "أماكن أخرى"),
    PublicPlaceSpec("t2", "T2", "T2", 323.3, 962.5, "Other named places", "أماكن أخرى"),
    PublicPlaceSpec("t3", "T3", "T3", 265.8, 834.3, "Other named places", "أماكن أخرى"),
    PublicPlaceSpec("t4", "T4", "T4", 204.6, 837.3, "Other named places", "أماكن أخرى"),
    PublicPlaceSpec("catering-a", "CATERING · A", "التموين · أ", 371.0, 1081.5, "Food service operations", "خدمات التموين", "CATERING"),
    PublicPlaceSpec("catering-b", "CATERING · B", "التموين · ب", 376.3, 1086.6, "Food service operations", "خدمات التموين", "CATERING"),
    PublicPlaceSpec("alpha-plan-storage", "ALPHA PLAN STORAGE", "مستودع ألفا بلان", 698.9, 280.6, "Offices and operations", "المكاتب والتشغيل"),

    # Staff, operations, and technical rooms
    PublicPlaceSpec("boh-g195", "G-195 BOH", "منطقة خلفية G-195", 352.8, 257.7, "Staff and operations", "الموظفون والتشغيل", "BOH"),
    PublicPlaceSpec("substation-g196", "G-196 SUBSTATION", "محطة فرعية G-196", 352.8, 280.7, "Technical rooms", "الغرف الفنية", "SUBSTATION"),
    PublicPlaceSpec("control-room-g197", "G-197 CONTROL ROOM", "غرفة التحكم G-197", 369.0, 280.7, "Technical rooms", "الغرف الفنية", "CONTROL ROOM"),
    PublicPlaceSpec("central-kitchen-g171", "G-171 CENTRAL KITCHEN", "المطبخ المركزي G-171", 384.8, 232.1, "Food service operations", "خدمات التموين", "CENTRAL KITCHEN"),
    PublicPlaceSpec("catering-admin-g147", "G-147 CATERING ADMIN", "إدارة التموين G-147", 511.8, 214.5, "Food service operations", "خدمات التموين", "CATERING ADMIN"),
    PublicPlaceSpec("security-control-g116", "G-116 SECURITY CONTROL", "مراقبة الأمن G-116", 687.8, 269.4, "Staff and operations", "الموظفون والتشغيل", "SECURITY CONTROL"),
    PublicPlaceSpec("office-g146", "G-146 OFFICE", "مكتب G-146", 523.2, 214.4, "Offices and operations", "المكاتب والتشغيل", "OFFICE"),
    PublicPlaceSpec("mod-g145", "G-145 MOD", "MOD G-145", 535.2, 214.4, "Staff and operations", "الموظفون والتشغيل", "MOD"),
    PublicPlaceSpec("royal-catering-g120", "G-120 SERVICES (ROYAL CATERING)", "خدمات التموين الملكي G-120", 641.8, 218.0, "Food service operations", "خدمات التموين", "SERVICES (ROYAL CATERING)"),
    PublicPlaceSpec("store", "STORE", "المتجر", 373.2, 246.6, "Staff and operations", "الموظفون والتشغيل"),
    PublicPlaceSpec("storage-office-g121", "G-121 STORAGE / OFFICE", "مستودع / مكتب G-121", 633.5, 228.4, "Offices and operations", "المكاتب والتشغيل", "STORAGE / OFFICE"),
    PublicPlaceSpec("security-control-g201", "G-201 SECURITY CONTROL", "مراقبة الأمن G-201", 645.6, 306.3, "Staff and operations", "الموظفون والتشغيل", "SECURITY CONTROL"),
    PublicPlaceSpec("boh-g194", "G-194 BOH", "منطقة خلفية G-194", 349.8, 241.8, "Staff and operations", "الموظفون والتشغيل", "BOH"),
    PublicPlaceSpec("it-room-g191", "G-191 IT ROOM", "غرفة تقنية المعلومات G-191", 348.2, 220.7, "Technical rooms", "الغرف الفنية", "IT ROOM"),
    PublicPlaceSpec("elec-room-g190", "G-190 ELEC ROOM", "غرفة الكهرباء G-190", 348.1, 216.3, "Technical rooms", "الغرف الفنية", "ELEC ROOM"),
    PublicPlaceSpec("elec-room-g56", "G-56 ELEC ROOM", "غرفة الكهرباء G-56", 959.5, 516.7, "Technical rooms", "الغرف الفنية", "ELEC ROOM"),
    PublicPlaceSpec("service-mep-g49", "G-49 SERVICE (MEP) BOH", "خدمات MEP الخلفية G-49", 964.8, 521.8, "Technical rooms", "الغرف الفنية", "SERVICE (MEP) BOH"),
    PublicPlaceSpec("catering-g47", "G-47 CATERING", "التموين G-47", 970.7, 531.7, "Food service operations", "خدمات التموين", "CATERING"),
    PublicPlaceSpec("ifas-cleaning-g85", "G-85 IFAS CLEANING STORAGE", "مستودع تنظيف IFAS G-85", 815.3, 388.0, "Staff and operations", "الموظفون والتشغيل", "IFAS CLEANING STORAGE"),
    PublicPlaceSpec("elec-room-g75", "G-75 ELEC ROOM", "غرفة الكهرباء G-75", 825.1, 382.0, "Technical rooms", "الغرف الفنية", "ELEC ROOM"),
    PublicPlaceSpec("service-mep-g73", "G-73 SERVICE (MEP) BOH", "خدمات MEP الخلفية G-73", 830.6, 387.3, "Technical rooms", "الغرف الفنية", "SERVICE (MEP) BOH"),
    PublicPlaceSpec("ifas-office-g67", "G-67 IFAS OFFICE", "مكتب IFAS G-67", 842.0, 407.5, "Offices and operations", "المكاتب والتشغيل", "IFAS OFFICE"),
    PublicPlaceSpec("catering-g71", "G-71 CATERING", "التموين G-71", 836.5, 397.6, "Food service operations", "خدمات التموين", "CATERING"),
    PublicPlaceSpec("ifas-cleaning-g138", "G-138 IFAS CLEANING STORAGE", "مستودع تنظيف IFAS G-138", 593.6, 220.9, "Staff and operations", "الموظفون والتشغيل", "IFAS CLEANING STORAGE"),
    PublicPlaceSpec("elec-room-g137", "G-137 ELEC ROOM", "غرفة الكهرباء G-137", 596.6, 210.1, "Technical rooms", "الغرف الفنية", "ELEC ROOM"),
    PublicPlaceSpec("service-mep-g135", "G-135 SERVICE (MEP) BOH", "خدمات MEP الخلفية G-135", 603.8, 210.0, "Technical rooms", "الغرف الفنية", "SERVICE (MEP) BOH"),
    PublicPlaceSpec("catering-g127", "G-127 CATERING", "التموين G-127", 615.5, 211.0, "Food service operations", "خدمات التموين", "CATERING"),
    PublicPlaceSpec("elec-room-g162", "G-162 ELEC ROOM", "غرفة الكهرباء G-162", 470.1, 210.3, "Technical rooms", "الغرف الفنية", "ELEC ROOM"),
    PublicPlaceSpec("it-room-g160", "G-160 IT ROOM", "غرفة تقنية المعلومات G-160", 477.0, 209.0, "Technical rooms", "الغرف الفنية", "IT ROOM"),
    PublicPlaceSpec("catering-g152", "G-152 CATERING", "التموين G-152", 488.6, 212.0, "Food service operations", "خدمات التموين", "CATERING"),
    PublicPlaceSpec("office-g115", "G-115 OFFICE", "مكتب G-115", 691.1, 266.0, "Offices and operations", "المكاتب والتشغيل", "OFFICE"),
    PublicPlaceSpec("storage-g109", "G-109 STORAGE", "مستودع G-109", 703.9, 276.1, "Staff and operations", "الموظفون والتشغيل", "STORAGE"),
    PublicPlaceSpec("elec-room-g108", "G-108 ELEC ROOM", "غرفة الكهرباء G-108", 713.4, 270.5, "Technical rooms", "الغرف الفنية", "ELEC ROOM"),
    PublicPlaceSpec("service-mep-g106", "G-106 SERVICE (MEP) BOH", "خدمات MEP الخلفية G-106", 718.9, 275.2, "Technical rooms", "الغرف الفنية", "SERVICE (MEP) BOH"),
    PublicPlaceSpec("office-g93", "G-93 OFFICE", "مكتب G-93", 730.3, 295.5, "Offices and operations", "المكاتب والتشغيل", "OFFICE"),
    PublicPlaceSpec("catering-g97", "G-97 CATERING", "التموين G-97", 725.9, 284.3, "Food service operations", "خدمات التموين", "CATERING"),
    PublicPlaceSpec("cleaning-storage-g21", "G-21 CLEANING STORAGE", "مستودع التنظيف G-21", 1051.0, 622.4, "Staff and operations", "الموظفون والتشغيل", "CLEANING STORAGE"),
    PublicPlaceSpec("it-room-g37", "G-37 IT ROOM", "غرفة تقنية المعلومات G-37", 1060.1, 619.8, "Technical rooms", "الغرف الفنية", "IT ROOM"),
    PublicPlaceSpec("elec-g35", "G-35 ELEC", "كهرباء G-35", 1063.3, 623.6, "Technical rooms", "الغرف الفنية", "ELEC"),
    PublicPlaceSpec("service-mep-g35", "G-35 SERVICE (MEP) / BOH", "خدمات MEP الخلفية G-35", 1069.0, 625.3, "Technical rooms", "الغرف الفنية", "SERVICE (MEP) / BOH"),
    PublicPlaceSpec("storage-g29", "G-29 STORAGE", "مستودع G-29", 1077.4, 642.6, "Staff and operations", "الموظفون والتشغيل", "STORAGE"),
    PublicPlaceSpec("catering-g34", "G-34 CATERING", "التموين G-34", 1071.5, 632.2, "Food service operations", "خدمات التموين", "CATERING"),
    PublicPlaceSpec("service-mep-g192", "G-192 SERVICE (MEP)", "خدمات MEP G-192", 348.1, 225.5, "Technical rooms", "الغرف الفنية", "SERVICE (MEP)"),
    PublicPlaceSpec("arab-expo-office-g12", "G-12 ARAB EXPO OFFICE", "مكتب عرب إكسبو G-12", 880.2, 625.1, "Offices and operations", "المكاتب والتشغيل", "ARAB EXPO OFFICE"),
    PublicPlaceSpec("arab-expo-office-g13", "G-13 ARAB EXPO OFFICE", "مكتب عرب إكسبو G-13", 870.2, 614.2, "Offices and operations", "المكاتب والتشغيل", "ARAB EXPO OFFICE"),
    PublicPlaceSpec("org-office-g03", "G-03 ORG. OFFICE (GLASS)", "مكتب التنظيم الزجاجي G-03", 987.6, 730.8, "Offices and operations", "المكاتب والتشغيل", "ORG. OFFICE (GLASS)"),
    PublicPlaceSpec("ve03-g207", "G-207 VE 03", "VE 03 (G-207)", 448.3, 211.8, "Access structures", "منشآت الوصول", "VE 03"),
    PublicPlaceSpec("ve02-g206", "G-206 VE 02", "VE 02 (G-206)", 742.1, 300.7, "Access structures", "منشآت الوصول", "VE 02"),
    PublicPlaceSpec("ve01-g205", "G-205 VE 01", "VE 01 (G-205)", 989.9, 545.7, "Access structures", "منشآت الوصول", "VE 01"),
    PublicPlaceSpec("tahaluf-operations-g15", "G-15 TAHALUF OPERATIONS OFFICE", "مكتب عمليات تحالف G-15", 920.7, 636.4, "Offices and operations", "المكاتب والتشغيل", "TAHALUF OPERATIONS OFFICE"),
    PublicPlaceSpec("security-control-g11", "G-11 SECURITY CONTROL", "مراقبة الأمن G-11", 908.9, 649.0, "Staff and operations", "الموظفون والتشغيل", "SECURITY CONTROL"),
    PublicPlaceSpec("org-office-g02", "G-02 ORG. OFFICE (GLASS)", "مكتب التنظيم الزجاجي G-02", 976.7, 720.0, "Offices and operations", "المكاتب والتشغيل", "ORG. OFFICE (GLASS)"),
    PublicPlaceSpec("freight-lift-lobby-08", "FREIGHT LIFT LOBBY 08", "ردهة مصعد الشحن 08", 370.0, 248.5, "Staff and operations", "الموظفون والتشغيل"),
    PublicPlaceSpec("electrical-e19-1", "ELECTRICAL ROOM E19-1", "غرفة الكهرباء E19-1", 813.3, 982.1, "Technical rooms", "الغرف الفنية"),
    PublicPlaceSpec("electrical-e23-1", "ELECTRICAL ROOM E23-1", "غرفة الكهرباء E23-1", 814.4, 1018.4, "Technical rooms", "الغرف الفنية"),
    PublicPlaceSpec("electrical-s19-1", "ELECTRICAL ROOM S19-1", "غرفة الكهرباء S19-1", 753.1, 1026.2, "Technical rooms", "الغرف الفنية"),
    PublicPlaceSpec("it-room-unnumbered", "IT ROOM · UNNUMBERED", "غرفة تقنية المعلومات · بدون رقم", 541.9, 1026.1, "Technical rooms", "الغرف الفنية", "IT ROOM"),
    PublicPlaceSpec("electrical-room-unnumbered", "ELECTRICAL ROOM · UNNUMBERED", "غرفة الكهرباء · بدون رقم", 533.7, 1026.1, "Technical rooms", "الغرف الفنية", "ELECTRICAL ROOM"),
    PublicPlaceSpec("storage-unnumbered", "STORAGE · UNNUMBERED", "مستودع · بدون رقم", 516.9, 1025.7, "Staff and operations", "الموظفون والتشغيل", "STORAGE"),
    PublicPlaceSpec("cleaning-storage-w17-1", "CLEANING STORAGE W17-1", "مستودع التنظيف W17-1", 475.7, 1010.9, "Staff and operations", "الموظفون والتشغيل"),
    PublicPlaceSpec("electrical-w15-1", "ELECTRICAL ROOM W15-1", "غرفة الكهرباء W15-1", 476.0, 996.2, "Technical rooms", "الغرف الفنية"),

    # Repeated generic rooms and named physical structures
    PublicPlaceSpec("lobby-01", "LOBBY · 01", "الردهة · 01", 470.6, 855.5, "Lobbies", "الردهات", "LOBBY"),
    PublicPlaceSpec("lobby-02", "LOBBY · 02", "الردهة · 02", 813.2, 856.1, "Lobbies", "الردهات", "LOBBY"),
    PublicPlaceSpec("lobby-03", "LOBBY · 03", "الردهة · 03", 813.0, 928.9, "Lobbies", "الردهات", "LOBBY"),
    PublicPlaceSpec("lobby-04", "LOBBY · 04", "الردهة · 04", 814.0, 989.7, "Lobbies", "الردهات", "LOBBY"),
    PublicPlaceSpec("lobby-05", "LOBBY · 05", "الردهة · 05", 698.4, 1032.3, "Lobbies", "الردهات", "LOBBY"),
    PublicPlaceSpec("lobby-06", "LOBBY · 06", "الردهة · 06", 554.6, 1032.6, "Lobbies", "الردهات", "LOBBY"),
    PublicPlaceSpec("lobby-07", "LOBBY · 07", "الردهة · 07", 478.8, 1032.3, "Lobbies", "الردهات", "LOBBY"),
    PublicPlaceSpec("lobby-08", "LOBBY · 08", "الردهة · 08", 469.8, 1008.0, "Lobbies", "الردهات", "LOBBY"),
    *tuple(
        PublicPlaceSpec(
            f"electrical-room-{index:02d}",
            f"ELECTRICAL ROOM · {index:02d}",
            f"غرفة الكهرباء · {index:02d}",
            x,
            y,
            "Technical rooms",
            "الغرف الفنية",
            "ELECTRICAL ROOM",
        )
        for index, (x, y) in enumerate(
            (
                (821.8, 833.3), (821.8, 914.4), (821.8, 957.6), (821.8, 1016.6),
                (906.4, 830.5), (906.4, 905.7), (906.4, 956.1), (906.4, 1012.1),
                (906.4, 1115.7), (753.5, 1035.3), (668.9, 1035.3), (596.8, 1035.3),
                (533.5, 1035.4), (382.1, 1041.0), (382.2, 992.6), (382.1, 865.9),
                (466.7, 1028.9), (466.8, 997.5), (466.7, 934.2), (466.8, 890.4),
            ),
            start=1,
        )
    ),
    PublicPlaceSpec("sub-station", "SUB STATION", "المحطة الفرعية", 1090.5, 656.1, "Technical rooms", "الغرف الفنية"),
    PublicPlaceSpec("ve-door-a1", "A1 VE Door", "باب VE A1", 369.6, 829.0, "Access structures", "منشآت الوصول"),
    PublicPlaceSpec("ve-door-a2", "A2 VE Door", "باب VE A2", 507.0, 1010.3, "Access structures", "منشآت الوصول"),
    PublicPlaceSpec("ve-door-a3", "A3 VE Door", "باب VE A3", 775.3, 1010.3, "Access structures", "منشآت الوصول"),
    PublicPlaceSpec("ve-door-a4", "A4 VE Door", "باب VE A4", 920.0, 866.6, "Access structures", "منشآت الوصول"),
    PublicPlaceSpec("ve-door-a5", "A5 VE Door", "باب VE A5", 920.0, 961.9, "Access structures", "منشآت الوصول"),
    PublicPlaceSpec("ve-door-a6", "A6 VE Door", "باب VE A6", 915.1, 1058.1, "Access structures", "منشآت الوصول"),
    PublicPlaceSpec("connection-hall-2-a", "Connection to Hall 2 · A", "الممر إلى الصالة 2 · أ", 850.4, 812.5, "Access structures", "منشآت الوصول", "Connection to Hall 2"),
    PublicPlaceSpec("connection-hall-2-b", "Connection to Hall 2 · B", "الممر إلى الصالة 2 · ب", 878.9, 812.5, "Access structures", "منشآت الوصول", "Connection to Hall 2"),
    PublicPlaceSpec("arch-tent-15x25", "ARCH TENT 15X25M / TUNNEL-8", "خيمة قوسية 15×25م / النفق 8", 633.8, 1013.1, "Temporary structures", "المنشآت المؤقتة", "ARCH TENT 15X25M"),
    PublicPlaceSpec("arch-tent-10x22-6", "ARCH TENT 10X22M / TUNNEL-6", "خيمة قوسية 10×22م / النفق 6", 499.0, 875.5, "Temporary structures", "المنشآت المؤقتة", "ARCH TENT 10X22M"),
    PublicPlaceSpec("arch-tent-10x22-7", "ARCH TENT 10X22M / TUNNEL-7", "خيمة قوسية 10×22م / النفق 7", 499.2, 970.8, "Temporary structures", "المنشآت المؤقتة", "ARCH TENT 10X22M"),
    PublicPlaceSpec("arch-tent-10x20", "ARCH TENT 10m x20m / TUNNEL 10", "خيمة قوسية 10×20م / النفق 10", 798.9, 878.5, "Temporary structures", "المنشآت المؤقتة", "ARCH TENT 10m x20m"),
    PublicPlaceSpec("arch-tent-5x20", "ARCH TENT 5m x20m / TUNNEL 9", "خيمة قوسية 5×20م / النفق 9", 790.1, 954.5, "Temporary structures", "المنشآت المؤقتة", "ARCH TENT 5m x20m"),

    # Meeting and conference rooms
    PublicPlaceSpec("meeting-g144", "G-144 MEETING ROOM", "غرفة الاجتماعات G-144", 547.0, 214.1, "Meeting rooms", "غرف الاجتماعات", "MEETING ROOM"),
    PublicPlaceSpec("meeting-g124", "G-124 MEETING ROOM", "غرفة الاجتماعات G-124", 626.3, 214.7, "Meeting rooms", "غرف الاجتماعات", "MEETING ROOM"),
    PublicPlaceSpec("conference-g65", "G-65 CONFERENCE", "قاعة المؤتمرات G-65", 874.2, 448.9, "Conference rooms", "قاعات المؤتمرات", "CONFERENCE"),
    PublicPlaceSpec("conference-g66", "G-66 CONFERENCE", "قاعة المؤتمرات G-66", 851.9, 426.5, "Conference rooms", "قاعات المؤتمرات", "CONFERENCE"),
    PublicPlaceSpec("conference-g63", "G-63 CONFERENCE", "قاعة المؤتمرات G-63", 919.0, 493.8, "Conference rooms", "قاعات المؤتمرات", "CONFERENCE"),
    PublicPlaceSpec("conference-g64", "G-64 CONFERENCE", "قاعة المؤتمرات G-64", 896.6, 471.4, "Conference rooms", "قاعات المؤتمرات", "CONFERENCE"),
    PublicPlaceSpec("meeting-e3-1", "MEETING ROOM E3-1", "غرفة الاجتماعات E3-1", 812.9, 835.9, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-e5-1", "MEETING ROOM E5-1", "غرفة الاجتماعات E5-1", 813.0, 848.5, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-e7-1", "MEETING ROOM E7-1", "غرفة الاجتماعات E7-1", 813.2, 863.1, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-e8-1", "MEETING ROOM E8-1", "غرفة الاجتماعات E8-1", 813.0, 874.2, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-e14-1", "MEETING ROOM E14-1", "غرفة الاجتماعات E14-1", 813.0, 933.0, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-e15-1", "MEETING ROOM E15-1", "غرفة الاجتماعات E15-1", 813.0, 940.2, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-e16-1", "MEETING ROOM E16-1", "غرفة الاجتماعات E16-1", 813.2, 949.5, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-e18-1", "MEETING ROOM E18-1", "غرفة الاجتماعات E18-1", 813.2, 971.1, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-e22-1", "MEETING ROOM E22-1", "غرفة الاجتماعات E22-1", 814.4, 1011.9, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-e21-1", "MEETING ROOM E21-1", "غرفة الاجتماعات E21-1", 813.0, 1004.6, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-e20-1", "MEETING ROOM E20-1", "غرفة الاجتماعات E20-1", 813.2, 995.9, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-s20-1", "MEETING ROOM S20-1", "غرفة الاجتماعات S20-1", 764.3, 1026.1, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-s18-1", "MEETING ROOM S18-1", "غرفة الاجتماعات S18-1", 737.4, 1026.3, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-s17-1", "MEETING ROOM S17-1", "غرفة الاجتماعات S17-1", 727.6, 1026.5, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-s16-1", "MEETING ROOM S16-1", "غرفة الاجتماعات S16-1", 719.0, 1026.5, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-s15-1", "MEETING ROOM S15-1", "غرفة الاجتماعات S15-1", 708.5, 1026.5, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-s14-1", "MEETING ROOM S14-1", "غرفة الاجتماعات S14-1", 699.0, 1026.4, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-s13-1", "MEETING ROOM S13-1", "غرفة الاجتماعات S13-1", 691.0, 1026.4, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-s9-1", "MEETING ROOM S9-1", "غرفة الاجتماعات S9-1", 575.2, 1026.7, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-s8-1", "MEETING ROOM S8-1", "غرفة الاجتماعات S8-1", 559.6, 1026.7, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-s5-1", "MEETING ROOM S5-1", "غرفة الاجتماعات S5-1", 525.4, 1027.0, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-w16-1", "MEETING ROOM W16-1", "غرفة الاجتماعات W16-1", 476.0, 1003.7, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-w14-1", "MEETING ROOM W14-1", "غرفة الاجتماعات W14-1", 474.2, 985.2, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-w12-1", "MEETING ROOM W12-1", "غرفة الاجتماعات W12-1", 474.3, 959.1, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-w11-1", "MEETING ROOM W11-1", "غرفة الاجتماعات W11-1", 474.2, 943.1, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-w5-1", "MEETING ROOM W5-1", "غرفة الاجتماعات W5-1", 474.2, 864.1, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-w4-1", "MEETING ROOM W4-1", "غرفة الاجتماعات W4-1", 476.0, 855.0, "Meeting rooms", "غرف الاجتماعات"),
    PublicPlaceSpec("meeting-w3-1", "MEETING ROOM W3-1", "غرفة الاجتماعات W3-1", 474.4, 845.3, "Meeting rooms", "غرف الاجتماعات"),
)


def run_poppler(pdf: Path, output: Path, *options: str) -> None:
    try:
        subprocess.run(
            ["pdftotext", *options, str(pdf), str(output)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise SystemExit("pdftotext (Poppler) is required to extract the map catalog.") from error
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr.strip() or "pdftotext failed") from error


def aligned_words(raw_text_path: Path, bbox_path: Path) -> list[Word]:
    raw_lines = raw_text_path.read_text(encoding="utf-8").splitlines()
    root = ET.parse(bbox_path).getroot()
    namespace = {"x": "http://www.w3.org/1999/xhtml"}
    bbox_words = root.findall(".//x:word", namespace)

    words: list[Word] = []
    bbox_index = 0
    for line_number, raw_line in enumerate(raw_lines, start=1):
        for token in raw_line.split():
            if bbox_index >= len(bbox_words):
                raise SystemExit("PDF text and bounding-box streams have different lengths.")
            bbox_word = bbox_words[bbox_index]
            bbox_index += 1
            bbox_text = bbox_word.text or ""
            if token != bbox_text:
                raise SystemExit(
                    f"PDF text alignment failed at word {bbox_index}: {token!r} != {bbox_text!r}"
                )
            words.append(
                Word(
                    text=token,
                    line=line_number,
                    x_min=float(bbox_word.attrib["xMin"]),
                    y_min=float(bbox_word.attrib["yMin"]),
                    x_max=float(bbox_word.attrib["xMax"]),
                    y_max=float(bbox_word.attrib["yMax"]),
                )
            )

    if bbox_index != len(bbox_words):
        raise SystemExit("PDF text and bounding-box streams have different lengths.")
    return words


def assigned_directory_bounds(words: list[Word]) -> tuple[int, int]:
    start = next(
        (index for index, word in enumerate(words) if word.text == FIRST_ASSIGNED_BOOTH),
        None,
    )
    if start is None:
        raise SystemExit(f"Could not find the first assigned booth {FIRST_ASSIGNED_BOOTH}.")

    for index in range(start, len(words) - 3):
        if (
            words[index].text == LAST_ASSIGNED_BOOTH
            and tuple(word.text for word in words[index + 1 : index + 3]) == LAST_ASSIGNED_NAME
        ):
            return start, index + 3
    raise SystemExit(f"Could not find the last assigned booth {LAST_ASSIGNED_BOOTH}.")


def parse_booths(words: list[Word]) -> list[Booth]:
    start, stop = assigned_directory_bounds(words)
    booths: list[Booth] = []
    current: Booth | None = None
    index = start

    while index < stop:
        word = words[index]
        code: str | None = None
        code_words = [word]
        consumed = 1
        if FULL_BOOTH_CODE.fullmatch(word.text):
            code = word.text
        elif (
            SPLIT_BOOTH_PREFIX.fullmatch(word.text)
            and index + 1 < stop
            and words[index + 1].text.isdigit()
        ):
            code_words.append(words[index + 1])
            code = word.text + words[index + 1].text
            consumed = 2

        if code:
            if current:
                booths.append(current)
            current = Booth(code=code, code_words=code_words, name_words=[])
            index += consumed
            continue

        if current:
            current.name_words.append(word)
        index += 1

    if current:
        booths.append(current)
    if not booths or any(not booth.name_words for booth in booths):
        raise SystemExit("The assigned booth directory contains an empty record.")
    if len({booth.code for booth in booths}) != len(booths):
        raise SystemExit("The assigned booth directory contains duplicate booth codes.")
    return booths


def line_groups(words: list[Word]) -> list[list[Word]]:
    groups: list[list[Word]] = []
    for word in words:
        if not groups or groups[-1][0].line != word.line:
            groups.append([word])
        else:
            groups[-1].append(word)
    return groups


def readable_name_parts(words: list[Word]) -> list[str]:
    """Preserve PDF lines while joining adjacent diagonal word fragments."""
    groups = line_groups(words)
    parts: list[str] = []
    group_index = 0
    while group_index < len(groups):
        group = groups[group_index]
        text = " ".join(word.text for word in group)
        if len(group) != 1:
            parts.append(text)
            group_index += 1
            continue

        merged = text
        previous = group[0]
        next_index = group_index + 1
        while next_index < len(groups) and len(groups[next_index]) == 1:
            following = groups[next_index][0]
            previous_x, previous_y = previous.center
            following_x, following_y = following.center
            delta_x = following_x - previous_x
            delta_y = following_y - previous_y
            fragment_distance = math.hypot(delta_x, delta_y)
            same_diagonal_word = (
                fragment_distance <= 1.25
                and abs(abs(delta_x) - abs(delta_y)) <= 0.28
                and abs(delta_x) >= 0.2
                and abs(delta_y) >= 0.2
            )
            if not same_diagonal_word:
                break
            merged += following.text
            previous = following
            next_index += 1
        parts.append(merged)
        group_index = next_index
    return parts


def hall_names(code: str) -> tuple[str, str]:
    if code.startswith("H1A."):
        return "Hall 1A", "الصالة 1A"
    hall = code[1]
    return f"Hall {hall}", f"الصالة {hall}"


def short_name(name: str, code: str) -> str:
    primary = name.split(" · ", 1)[0]
    if len(primary) <= 34:
        return primary
    return f"{primary[:31].rstrip()}…"


def normalized_aliases(name: str, parts: list[str], code: str) -> list[str]:
    candidates = [code, name, *parts]
    aliases: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = " ".join(candidate.split()).strip(" ·")
        key = candidate.casefold()
        # Avoid indexing single layout fragments such as "of" or "IT". The
        # complete printed name and booth code remain available for every booth.
        if len(candidate) < 3 or key in seen:
            continue
        seen.add(key)
        aliases.append(candidate)
    return aliases


def booth_record(booth: Booth) -> dict[str, object]:
    parts = readable_name_parts(booth.name_words)
    extracted_name = " ".join(parts)
    name = NAME_OVERRIDES.get(booth.code, extracted_name)
    zone, zone_ar = hall_names(booth.code)
    center_x = sum(word.center[0] for word in booth.code_words) / len(booth.code_words)
    center_y = sum(word.center[1] for word in booth.code_words) / len(booth.code_words)
    identifier = "booth-" + re.sub(r"[^a-z0-9]+", "-", booth.code.casefold()).strip("-")
    return {
        "id": identifier,
        "boothCode": booth.code,
        "name": name,
        # Exhibitor brands in this plan are printed in English. Preserve the
        # source spelling in Arabic mode instead of fabricating translations.
        "nameAr": name,
        "shortName": short_name(name, booth.code),
        "shortNameAr": short_name(name, booth.code),
        "x": round(center_x * PDF_TO_MAP_SCALE, 1),
        "y": round(center_y * PDF_TO_MAP_SCALE, 1),
        "zone": zone,
        "zoneAr": zone_ar,
        "aliases": normalized_aliases(name, parts, booth.code),
        "printedOnPdf": True,
    }


def public_place_record(place: PublicPlaceSpec) -> dict[str, object]:
    source_label = place.source_label or place.name
    aliases = normalized_aliases(
        place.name,
        [source_label, place.name_ar],
        place.id,
    )
    return {
        "id": place.id,
        "name": place.name,
        "nameAr": place.name_ar,
        "shortName": short_name(place.name, place.id),
        "shortNameAr": short_name(place.name_ar, place.id),
        "x": place.x,
        "y": place.y,
        "zone": place.zone,
        "zoneAr": place.zone_ar,
        "aliases": aliases,
        "featured": place.featured,
        "printedOnPdf": True,
        "sourceLabel": source_label,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pdf = args.pdf.resolve()
    output = args.output.resolve()
    if not pdf.is_file():
        raise SystemExit(f"PDF not found: {pdf}")

    with tempfile.TemporaryDirectory(prefix="leap-map-") as temporary_directory:
        temporary = Path(temporary_directory)
        raw_text = temporary / "plan.txt"
        raw_bbox = temporary / "plan.html"
        run_poppler(pdf, raw_text, "-raw")
        run_poppler(pdf, raw_bbox, "-raw", "-bbox")
        words = aligned_words(raw_text, raw_bbox)
        booths = parse_booths(words)

    records = [booth_record(booth) for booth in booths]
    public_records = [public_place_record(place) for place in PUBLIC_PLACE_SPECS]
    all_ids = [record["id"] for record in (*public_records, *records)]
    if len(set(all_ids)) != len(all_ids):
        raise SystemExit("Generated public and booth destination IDs must be unique.")
    payload = {
        "source": pdf.name,
        "page": 1,
        "coordinateScale": PDF_TO_MAP_SCALE,
        "assignedBoothCount": len(records),
        "publicPlaceCount": len(public_records),
        "publicPlaces": public_records,
        "places": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {len(records)} assigned booths and {len(public_records)} "
        f"public PDF destinations to {output}"
    )


if __name__ == "__main__":
    main()
